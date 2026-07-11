"""객체 스토리지형 멱등 전략: 결정적 파일명 + atomic replace.

같은 unit은 같은 파일명({unit_id}.parquet) → 재실행 = 덮어쓰기 = 중복 없음.
구현: docs/plans/2026-07-08-m1-core-foundation.md Task 7
"""

import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from arsenal_core.state import UnitSpec


class ParquetSink:
    def __init__(self, path: Path) -> None:
        self._dir = path

    def write(self, unit: UnitSpec, batch: pa.RecordBatch) -> None:
        """{dir}/{unit_id}.parquet.tmp에 쓰고 os.replace로 원자 교체."""
        self._dir.mkdir(parents=True, exist_ok=True)
        final = self._dir / f"{unit.unit_id}.parquet"
        tmp = self._dir / f"{unit.unit_id}.parquet.tmp"
        pq.write_table(pa.Table.from_batches([batch]), tmp)  # pyright: ignore[reportUnknownMemberType]
        os.replace(tmp, final)  # POSIX atomic — 부분 쓰기가 결과로 보이지 않음
