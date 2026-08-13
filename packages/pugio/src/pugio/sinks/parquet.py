"""객체 스토리지형 멱등 전략: 결정적 파일명 + atomic replace.

같은 unit은 같은 파일명({unit_id}.parquet) → 재실행 = 덮어쓰기 = 중복 없음.
구현: docs/plans/2026-07-08-m1-core-foundation.md Task 7
"""

import os
import uuid
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from arsenal_core.state import UnitSpec


class ParquetSink:
    def __init__(self, path: Path) -> None:
        self._dir = path

    def _tmp_path(self, unit: UnitSpec) -> Path:
        """writer마다 고유한 임시 경로.

        결정적 이름({unit_id}.parquet.tmp)이면 같은 유닛을 동시에 쓰는 두 writer가
        같은 파일에 섞어 쓴 뒤 각자 replace 한다 — rename은 원자적이어도 옮겨지는
        내용이 이미 깨져 있다. pid+uuid로 writer를 분리하면 각자 온전한 파일을 만들고
        replace는 마지막 것이 이긴다(같은 유닛이므로 내용은 동등).
        """
        return self._dir / f"{unit.unit_id}.{os.getpid()}.{uuid.uuid4().hex[:8]}.parquet.tmp"

    def write(self, unit: UnitSpec, batch: pa.RecordBatch) -> None:
        """고유 임시 파일에 쓰고 os.replace로 {dir}/{unit_id}.parquet에 원자 교체."""
        self._dir.mkdir(parents=True, exist_ok=True)
        final = self._dir / f"{unit.unit_id}.parquet"
        tmp = self._tmp_path(unit)
        try:
            pq.write_table(pa.Table.from_batches([batch]), tmp)  # pyright: ignore[reportUnknownMemberType]
            os.replace(tmp, final)  # POSIX atomic — 부분 쓰기가 결과로 보이지 않음
        finally:
            # 쓰기 실패 시 고아 임시 파일을 남기지 않는다 (성공 시엔 이미 replace됨)
            tmp.unlink(missing_ok=True)
