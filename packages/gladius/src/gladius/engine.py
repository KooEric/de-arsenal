"""DuckDB 실행 엔진 (M3 Task 3.3~3.6). 엔진은 빌린다 — 우리는 컴파일과 신뢰성만.

Parquet in → (compile_sql 결과 실행) → Parquet out. Arrow 경유 zero-copy.
query()는 즉석 SQL(미니 DWH) — 원클릭 경험의 "아하 모먼트".
"""

from pathlib import Path

import pyarrow as pa

from gladius.spec import TransformSpec


def run_transform(spec: TransformSpec) -> Path:
    """변환을 실행하고 output 경로를 반환한다."""
    raise NotImplementedError("M3 Task 3.3 — docs/plans/2026-07-08-m3-gladius.md")


def query(sql: str) -> pa.Table:
    """Parquet 레이크에 즉석 SQL — FROM './data/*.parquet' 경로 직접 참조.

    in-memory DuckDB, 상태 없음. DuckDB 에러는 FatalError로 변환.
    """
    raise NotImplementedError("M3 Task 3.6 — docs/plans/2026-07-08-m3-gladius.md")
