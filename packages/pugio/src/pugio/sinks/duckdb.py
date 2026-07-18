"""DuckDB 파일 싱크 — temp view(zero-copy) → DELETE+INSERT 트랜잭션으로 멱등 upsert.

멱등 전략: merge_key로 식별되는 기존 행을 지우고 batch를 통째로 다시 넣는다,
한 트랜잭션 안에서. 같은 unit을 재실행해도 결과가 바뀌지 않는다(같은 batch →
같은 delete+insert → 같은 최종 상태) — MERGE 문법보다 단순하고 DuckDB에서
안정적으로 동작한다. base.py Sink 프로토콜의 계약(멱등·atomic)을 만족한다.
"""

import contextlib

import duckdb
import pyarrow as pa

from arsenal_core.errors import RetryableError
from arsenal_core.spec.models import DuckDBSinkSpec
from arsenal_core.state import UnitSpec


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class DuckDBSink:
    def __init__(self, spec: DuckDBSinkSpec) -> None:
        self._spec = spec

    def write(self, unit: UnitSpec, batch: pa.RecordBatch) -> None:
        con = duckdb.connect(str(self._spec.path))
        try:
            staging = pa.Table.from_batches([batch])
            con.register("staging", staging)  # Arrow 배치를 zero-copy로 등록
            table = _quote_ident(self._spec.table)
            keys = " AND ".join(
                f"{table}.{_quote_ident(k)} = s.{_quote_ident(k)}" for k in self._spec.merge_key
            )
            con.execute(f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM staging LIMIT 0")
            con.execute("BEGIN")
            con.execute(f"DELETE FROM {table} WHERE EXISTS (SELECT 1 FROM staging s WHERE {keys})")
            con.execute(f"INSERT INTO {table} SELECT * FROM staging")
            con.execute("COMMIT")
        except Exception as e:
            with contextlib.suppress(Exception):  # 롤백 실패는 무시하고 원본 에러를 전파한다
                con.execute("ROLLBACK")
            raise RetryableError(f"duckdb write failed for unit {unit.unit_id}: {e}") from e
        finally:
            con.close()
