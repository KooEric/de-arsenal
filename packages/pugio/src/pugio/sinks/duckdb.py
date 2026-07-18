"""DuckDB 파일 싱크 — temp view(zero-copy) → DELETE+INSERT 트랜잭션으로 멱등 upsert.

멱등 전략: merge_key로 식별되는 기존 행을 지우고 batch를 통째로 다시 넣는다,
한 트랜잭션 안에서. 같은 unit을 재실행해도 결과가 바뀌지 않는다(같은 batch →
같은 delete+insert → 같은 최종 상태) — MERGE 문법보다 단순하고 DuckDB에서
안정적으로 동작한다. base.py Sink 프로토콜의 계약(멱등·atomic)을 만족한다.

in-batch 중복 merge_key (M2-F FIX 2): 한 batch 안에 같은 merge_key가 여러 번
나오면(예: 소스가 지연 수정 이벤트를 여러 개 보냄) INSERT를 그대로 하면 유니크
제약이 없는 DuckDB 특성상 중복 행이 그대로 쌓인다 — postgres의 ON CONFLICT는
row 단위로 처리되어 last-wins 단일 행이 되는 것과 어긋난다. 두 싱크가 동일한
결과(merge_key당 정확히 한 행, batch 내 마지막 값)를 내도록, 등록 직전에
batch 행 순서를 나타내는 합성 컬럼(__arsenal_seq)을 붙이고 INSERT 시
QUALIFY row_number() OVER (PARTITION BY merge_key ORDER BY __arsenal_seq DESC) = 1
로 각 키의 마지막 행만 남긴다. 원본 스키마에 없는 컬럼이므로 CREATE TABLE/INSERT
모두 명시적 컬럼 리스트로 __arsenal_seq를 제외한다.

에러 분류 (M2-F FIX 1): duckdb 예외를 뭉뚱그려 RetryableError로 묶으면 파서/바인더/
카탈로그/제약 오류 같은 영구적 설정 버그까지 재시도하게 된다 — 재시도해도 같은
스키마 불일치가 계속 실패할 뿐이다. IO/트랜잭션 충돌(락/디스크)만 일시적이라
RetryableError로, 나머지 duckdb.Error 서브클래스와 그 외 모든 예외는 FatalError로
분류한다.
"""

import contextlib

import duckdb
import pyarrow as pa

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.spec.models import DuckDBSinkSpec
from arsenal_core.state import UnitSpec

_SEQ_COL = "__arsenal_seq"


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class DuckDBSink:
    def __init__(self, spec: DuckDBSinkSpec) -> None:
        self._spec = spec

    def write(self, unit: UnitSpec, batch: pa.RecordBatch) -> None:
        con = duckdb.connect(str(self._spec.path))
        try:
            columns = list(batch.schema.names)
            seq = pa.array(range(batch.num_rows), type=pa.int64())
            staging = pa.Table.from_batches([batch]).append_column(_SEQ_COL, seq)
            con.register("staging", staging)  # Arrow 배치를 zero-copy로 등록
            table = _quote_ident(self._spec.table)
            col_list = ", ".join(_quote_ident(c) for c in columns)
            partition = ", ".join(_quote_ident(k) for k in self._spec.merge_key)
            keys = " AND ".join(
                f"{table}.{_quote_ident(k)} = s.{_quote_ident(k)}" for k in self._spec.merge_key
            )
            con.execute(
                f"CREATE TABLE IF NOT EXISTS {table} AS SELECT {col_list} FROM staging LIMIT 0"
            )
            con.execute("BEGIN")
            con.execute(f"DELETE FROM {table} WHERE EXISTS (SELECT 1 FROM staging s WHERE {keys})")
            con.execute(
                f"INSERT INTO {table} SELECT {col_list} FROM staging "
                f"QUALIFY row_number() OVER "
                f"(PARTITION BY {partition} ORDER BY {_quote_ident(_SEQ_COL)} DESC) = 1"
            )
            con.execute("COMMIT")
        except (duckdb.IOException, duckdb.TransactionException) as e:
            # 락 경합/디스크 IO — 일시적, 재시도하면 성공할 수 있다.
            with contextlib.suppress(Exception):  # 롤백 실패는 무시하고 원본 에러를 전파한다
                con.execute("ROLLBACK")
            raise RetryableError(f"duckdb write failed for unit {unit.unit_id}: {e}") from e
        except Exception as e:
            # 파서/바인더/카탈로그/제약 위반 등 나머지 duckdb.Error 및 그 외 모든 예외 —
            # 설정/계약 문제라 재시도해도 소용없다.
            with contextlib.suppress(Exception):
                con.execute("ROLLBACK")
            raise FatalError(f"duckdb write failed for unit {unit.unit_id}: {e}") from e
        finally:
            con.close()
