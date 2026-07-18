"""Postgres 싱크 — merge_key 기준 INSERT ... ON CONFLICT DO UPDATE로 멱등 upsert.

첫 write에서 Arrow 스키마로 대상 테이블을 생성한다(PRIMARY KEY = merge_key —
ON CONFLICT가 요구하는 유니크 제약). 이후 write마다 executemany로 배치를 적재하며
merge_key가 충돌하면 나머지 컬럼을 EXCLUDED 값으로 덮어쓴다 — 같은 batch를
재실행해도 최종 상태가 같다(base.py Sink 프로토콜 계약: 멱등·atomic). 한
트랜잭션(con.transaction()) 안에서 스키마 보장 + upsert를 모두 수행한다.

에러 분류 (arsenal_core.errors):
  - DSN 환경변수 미설정, 지원하지 않는 Arrow 타입, SQL 프로그래밍 오류(psycopg.Error
    일반) → FatalError. 설정/계약 문제라 재시도해도 소용없다.
  - 연결 실패(psycopg.OperationalError) → RetryableError. 네트워크/일시 장애로
    분류해 재시도 대상이 된다.

식별자는 psycopg.sql.Identifier로 안전하게 구성한다(문자열 직접 보간 금지) —
gladius.compile.ident.quote_ident와 동일한 목적이지만, pugio는 gladius에
의존하지 않으므로 psycopg 자체 안전 quoting API를 그대로 쓴다.
"""

import os

import psycopg
import pyarrow as pa
from psycopg import sql

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.spec.models import PostgresSinkSpec
from arsenal_core.state import UnitSpec


def _pg_type(field: pa.Field) -> sql.SQL:
    """고정 화이트리스트만 반환한다 — sql.SQL은 LiteralString만 받는 안전 API라서,
    호출부마다 리터럴로 감싸 반환해야 임의 문자열이 SQL 조각으로 흘러들지 않는다."""
    t = field.type
    if pa.types.is_int64(t):
        return sql.SQL("bigint")
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return sql.SQL("text")
    if pa.types.is_float64(t):
        return sql.SQL("double precision")
    if pa.types.is_boolean(t):
        return sql.SQL("boolean")
    if pa.types.is_timestamp(t):
        return sql.SQL("timestamptz")
    raise FatalError(f"unsupported arrow type for postgres sink: column={field.name!r} type={t!r}")


class PostgresSink:
    def __init__(self, spec: PostgresSinkSpec) -> None:
        self._spec = spec

    def write(self, unit: UnitSpec, batch: pa.RecordBatch) -> None:
        dsn = os.environ.get(self._spec.dsn_env)
        if not dsn:
            raise FatalError(
                f"environment variable {self._spec.dsn_env!r} is not set (postgres sink dsn)"
            )
        try:
            con = psycopg.connect(dsn)
        except psycopg.OperationalError as e:
            raise RetryableError(f"postgres connection failed for unit {unit.unit_id}: {e}") from e

        try:
            with con.transaction():
                self._ensure_table(con, batch.schema)
                self._upsert(con, batch)
        except psycopg.OperationalError as e:
            raise RetryableError(f"postgres write failed for unit {unit.unit_id}: {e}") from e
        except FatalError:
            raise
        except psycopg.Error as e:
            raise FatalError(f"postgres write failed for unit {unit.unit_id}: {e}") from e
        finally:
            con.close()

    def _ensure_table(self, con: psycopg.Connection, schema: pa.Schema) -> None:
        columns = [sql.SQL("{} {}").format(sql.Identifier(f.name), _pg_type(f)) for f in schema]
        pk = sql.SQL(", ").join(sql.Identifier(k) for k in self._spec.merge_key)
        stmt = sql.SQL("CREATE TABLE IF NOT EXISTS {table} ({cols}, PRIMARY KEY ({pk}))").format(
            table=sql.Identifier(self._spec.table),
            cols=sql.SQL(", ").join(columns),
            pk=pk,
        )
        con.execute(stmt)

    def _upsert(self, con: psycopg.Connection, batch: pa.RecordBatch) -> None:
        cols = batch.schema.names
        merge_key = set(self._spec.merge_key)
        update_cols = [c for c in cols if c not in merge_key]
        if update_cols:
            set_clause = sql.SQL(", ").join(
                sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c)) for c in update_cols
            )
            conflict_action = sql.SQL("DO UPDATE SET {}").format(set_clause)
        else:
            conflict_action = sql.SQL("DO NOTHING")
        stmt = sql.SQL(
            "INSERT INTO {table} ({cols}) VALUES ({vals}) ON CONFLICT ({conflict_cols}) {action}"
        ).format(
            table=sql.Identifier(self._spec.table),
            cols=sql.SQL(", ").join(sql.Identifier(c) for c in cols),
            vals=sql.SQL(", ").join(sql.Placeholder() for _ in cols),
            conflict_cols=sql.SQL(", ").join(sql.Identifier(k) for k in self._spec.merge_key),
            action=conflict_action,
        )
        rows = batch.to_pylist()
        with con.cursor() as cur:
            cur.executemany(stmt, [tuple(row[c] for c in cols) for row in rows])
