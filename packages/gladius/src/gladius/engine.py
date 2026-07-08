"""DuckDB 실행 엔진 (M3 Task 3.3). 엔진은 빌린다 — 우리는 컴파일과 신뢰성만.

Parquet in → (compile_sql 결과 실행) → Parquet out. Arrow 경유 zero-copy.
P1: `gladius query "SELECT ..."` 즉석 쿼리 모드(미니 DWH)도 이 모듈이 담당.
"""

from pathlib import Path

from gladius.spec import TransformSpec


def run_transform(spec: TransformSpec) -> Path:
    """변환을 실행하고 output 경로를 반환한다."""
    raise NotImplementedError("M3 Task 3.3 — docs/04-implementation-plan.md")
