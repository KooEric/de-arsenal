"""Postgres 싱크 — merge_key 기준 INSERT ... ON CONFLICT DO UPDATE로 멱등 upsert.

첫 write에서 Arrow 스키마로 대상 테이블을 생성한다(PRIMARY KEY = merge_key —
ON CONFLICT가 요구하는 유니크 제약). 이후 write마다 executemany로 배치를 적재하며
merge_key가 충돌하면 나머지 컬럼을 EXCLUDED 값으로 덮어쓴다 — 같은 batch를
재실행해도 최종 상태가 같다(base.py Sink 프로토콜 계약: 멱등·atomic). 한
트랜잭션(con.transaction()) 안에서 스키마 보장 + upsert를 모두 수행한다.

에러 분류 (arsenal_core.errors):
  - DSN 환경변수 미설정, 지원하지 않는 Arrow 타입(naive timestamp 포함), SQL
    프로그래밍 오류(psycopg.Error 일반) → FatalError. 설정/계약 문제라 재시도해도
    소용없다.
  - psycopg.OperationalError는 sqlstate로 다시 나눈다 (M2-F FIX 3): 인증 실패
    (28xxx, 예 28P01 invalid_password)나 대상 db/schema 부재(3D000/3F000)는
    설정 오류라 FatalError — 비밀번호가 틀렸는데 재시도해봐야 계속 틀린 채다.
    그 외(연결 거부/타임아웃 등 sqlstate 없는 클라이언트 레벨 오류)는 네트워크/
    일시 장애로 보고 RetryableError.
    실측(psycopg 3.3.4): connect() 단계에서 나는 OperationalError는 PGresult가
    아직 없어 e.sqlstate/e.diag.sqlstate가 항상 None이다 — 인증 실패든 연결
    거부든 구조화된 코드가 없다. sqlstate가 있으면(=쿼리 실행 단계 오류) 그걸
    최우선으로 쓰고, 없으면 libpq가 내려준 원문 FATAL 메시지("password
    authentication failed", "database ... does not exist" 등)로 폴백 판별한다.

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

_FATAL_OPERATIONAL_SQLSTATE_PREFIXES = ("28",)  # invalid_authorization_specification 계열
_FATAL_OPERATIONAL_SQLSTATES = frozenset({"3D000", "3F000"})  # invalid catalog/schema name

# connect() 단계 실패는 sqlstate가 없다(위 docstring 참조) — libpq가 내려준 원문
# FATAL 메시지로 폴백 판별한다. 인증 실패/존재하지 않는 db·role은 설정 오류다.
_FATAL_CONNECT_MESSAGE_MARKERS = (
    "password authentication failed",
    "no pg_hba.conf entry",
)


def _looks_like_fatal_connect_error(message: str) -> bool:
    if any(marker in message for marker in _FATAL_CONNECT_MESSAGE_MARKERS):
        return True
    return "does not exist" in message and ('database "' in message or 'role "' in message)


def _classify_operational_error(
    e: psycopg.OperationalError,
) -> type[FatalError] | type[RetryableError]:
    """sqlstate(있으면)로, 없으면 원문 메시지로 영구(설정) 오류와 일시(네트워크)
    오류를 나눈다 (M2-F FIX 3).

    인증 실패(28xxx)나 대상 db/schema 부재(3D000/3F000)는 재시도해도 그대로
    실패하는 설정 문제 — Fatal. 연결 거부/타임아웃처럼 서버에 닿기 전에 나는
    클라이언트 레벨 오류는 sqlstate도 없고 폴백 마커에도 안 걸리니, 네트워크
    일시 장애로 보고 Retryable로 남긴다.
    """
    sqlstate = getattr(e, "sqlstate", None)
    if sqlstate is not None:
        if sqlstate.startswith(_FATAL_OPERATIONAL_SQLSTATE_PREFIXES) or (
            sqlstate in _FATAL_OPERATIONAL_SQLSTATES
        ):
            return FatalError
        return RetryableError
    if _looks_like_fatal_connect_error(str(e)):
        return FatalError
    return RetryableError


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
        # naive timestamp는 postgres에 꽂히는 순간 서버(세션) 타임존 기준으로 암묵
        # 해석되어 값이 조용히 shift될 수 있다 (M2-F FIX 6) — 업스트림에서 UTC로
        # 정규화하도록 강제한다.
        if t.tz is None:
            raise FatalError(
                f"column {field.name!r}: naive timestamp not supported; "
                "normalize to a timezone (UTC) upstream"
            )
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
            error_cls = _classify_operational_error(e)
            raise error_cls(f"postgres connection failed for unit {unit.unit_id}: {e}") from e
        except psycopg.Error as e:
            # M2 최종 리뷰 FIX 5: OperationalError만 잡으면 malformed DSN 같은
            # psycopg.ProgrammingError(구성 오류)가 분류 없이 그대로 샌다 — 재시도해도
            # 문법은 그대로 틀린 채라 Fatal로 분류한다. psycopg 예외가 unwrapped로
            # escape하는 경로를 남기지 않는다.
            raise FatalError(f"postgres connection failed for unit {unit.unit_id}: {e}") from e

        try:
            with con.transaction():
                self._ensure_table(con, batch.schema)
                self._upsert(con, batch)
        except psycopg.OperationalError as e:
            error_cls = _classify_operational_error(e)
            raise error_cls(f"postgres write failed for unit {unit.unit_id}: {e}") from e
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
