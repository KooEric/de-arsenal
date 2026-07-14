"""Source 커넥터 계약. 커넥터는 재시도·상태·멱등을 모른다 — 러너가 흡수한다.

새 커넥터 추가 = 이 프로토콜 구현 + 스펙 모델 등록 (docs/02-architecture.md).
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

import pyarrow as pa

from arsenal_core.state import UnitSpec


@dataclass(frozen=True)
class FetchResult:
    batch: pa.RecordBatch | None  # None = 데이터 없음
    exhausted: bool  # True = 이 unit이 마지막, 더 이상 unit 없음


class Source(Protocol):
    def units(self) -> Iterator[UnitSpec]:
        """Unit of Work를 lazy하게 열거. 끝을 모르면 무한 generator.

        offset 모드는 끝을 미리 알 수 없으므로 fetch()의 exhausted가 종료 신호.
        M2 cursor 모드는 커서 값 자체를 unit_key로 사용한다.
        """
        ...

    def fetch(self, unit: UnitSpec) -> FetchResult:
        """unit 하나를 Arrow로. 실패는 분류된 예외(arsenal_core.errors)로 던진다."""
        ...

    # 선택적 훅: 커넥션 풀 등 리소스를 든 소스(RestSource)만 구현한다. Protocol에
    # 필수 멤버로 넣으면 file/database/python 소스도 구현을 강제받으므로, 대신
    # 구조적으로는 선택 사항으로 두고 러너가 getattr(source, "close", None)으로
    # 방어적으로 호출한다 (pugio.runner.run_pipeline 참조).
