"""DuckDB 실행 엔진 (M3 Task 3.3~3.6). 엔진은 빌린다 — 우리는 컴파일과 신뢰성만.

Parquet in → (compile_sql 결과 실행) → Parquet out. Arrow 경유 zero-copy.
query()는 즉석 SQL(미니 DWH) — 원클릭 경험의 "아하 모먼트".
"""

import os
import shutil
from pathlib import Path

import duckdb
import pyarrow as pa

from arsenal_core.errors import FatalError
from gladius.compile import compile_sql
from gladius.spec import TransformSpec


def run_transform(spec: TransformSpec) -> Path:
    """변환을 실행하고 output 경로를 반환한다.

    전체 재계산 의미론(P0): 매 실행은 output을 통째로 다시 쓴다. 임시 디렉터리에
    쓰고 os.replace로 원자 교체 — 실패한 실행이 이전 성공 출력을 훼손하지 않는다.
    """
    sql = compile_sql(spec)
    con = duckdb.connect()  # in-memory, 상태 없음
    con.execute(f"PRAGMA threads={os.cpu_count()}")
    tmp = spec.output.with_name(spec.output.name + ".tmp")
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        con.execute(
            f"COPY ({sql}) TO '{tmp / 'part-0.parquet'}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    except duckdb.Error as e:
        raise FatalError(f"transform failed: {e}\n--- compiled SQL ---\n{sql}") from e
    finally:
        con.close()
    if spec.output.exists():
        shutil.rmtree(spec.output)  # 변환 출력은 전체 재계산 의미론 (P0)
    os.replace(tmp, spec.output)
    return spec.output


def query(sql: str) -> pa.Table:
    """Parquet 레이크에 즉석 SQL — FROM './data/*.parquet' 경로 직접 참조.

    in-memory DuckDB, 상태 없음. DuckDB 에러는 FatalError로 변환.
    """
    raise NotImplementedError("M3 Task 3.6 — docs/plans/2026-07-08-m3-gladius.md")
