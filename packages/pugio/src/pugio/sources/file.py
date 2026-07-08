"""로컬 파일 소스 — csv/jsonl/excel → Arrow. 분석가의 1번 고통의 입구.

파일 하나 = unit 하나 (unit_key = 글롭 루트 기준 상대 경로) → 멱등이 공짜.
units()는 유한 generator — 러너는 exhausted 없이 루프 자연 종료로 끝난다.
구현: docs/plans/2026-07-08-m2-pugio-complete.md Task 2.11
"""

from collections.abc import Iterator

from arsenal_core.spec.models import FileSourceSpec
from arsenal_core.state import UnitSpec
from pugio.sources.base import FetchResult


class FileSource:
    def __init__(self, spec: FileSourceSpec, *, pipeline: str) -> None:
        self._spec = spec
        self._pipeline = pipeline

    def units(self) -> Iterator[UnitSpec]:
        """글롭 매칭 파일을 정렬 순서로 열거 — 결정적 순서."""
        raise NotImplementedError("M2 Task 2.11 — docs/plans/2026-07-08-m2-pugio-complete.md")

    def fetch(self, unit: UnitSpec) -> FetchResult:
        """format(auto=확장자 판별)에 따라 csv/jsonl/excel을 Arrow로.

        파싱 불가 파일은 FatalError. excel은 optional extra(pugio[excel] → fastexcel).
        """
        raise NotImplementedError("M2 Task 2.11 — docs/plans/2026-07-08-m2-pugio-complete.md")
