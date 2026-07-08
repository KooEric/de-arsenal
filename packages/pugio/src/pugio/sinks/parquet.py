"""객체 스토리지형 멱등 전략: 결정적 파일명 + atomic replace.

같은 unit은 같은 파일명({unit_id}.parquet) → 재실행 = 덮어쓰기 = 중복 없음.
구현: docs/plans/2026-07-08-m1-core-foundation.md Task 7
"""

from pathlib import Path

import pyarrow as pa

from arsenal_core.state import UnitSpec


class ParquetSink:
    def __init__(self, path: Path) -> None:
        self._dir = path

    def write(self, unit: UnitSpec, batch: pa.RecordBatch) -> None:
        """{dir}/{unit_id}.parquet.tmp에 쓰고 os.replace로 원자 교체."""
        raise NotImplementedError("M1 Task 7 — docs/plans/2026-07-08-m1-core-foundation.md")
