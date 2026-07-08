"""Sink 커넥터 계약. 핵심은 멱등 — 같은 unit 재호출 시 결과가 변하지 않는다.

끝점별 멱등 전략 (docs/02-architecture.md):
- 파일/객체 스토리지: 결정적 파일명 덮어쓰기
- MERGE 지원 DB (duckdb/postgres, M2): temp table → MERGE
모든 sink는 공통 계약 테스트 스위트를 통과해야 한다 (docs/05-testing-plan.md).
"""

from typing import Protocol

import pyarrow as pa

from arsenal_core.state import UnitSpec


class Sink(Protocol):
    def write(self, unit: UnitSpec, batch: pa.RecordBatch) -> None:
        """멱등하게 쓴다. 부분 쓰기가 결과로 보여서도 안 된다 (atomic)."""
        ...
