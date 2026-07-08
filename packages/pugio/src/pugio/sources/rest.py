"""REST 소스 — M1은 offset 페이지네이션 1종.

구현: docs/plans/2026-07-08-m1-core-foundation.md Task 6
M2 확장: page/cursor 모드, encoding, rate limiter, AuthProvider 연동 (docs/01-scope.md M2)
"""

from collections.abc import Iterator

import httpx

from arsenal_core.spec.models import SourceSpec
from arsenal_core.state import UnitSpec
from pugio.sources.base import FetchResult


class RestSource:
    def __init__(self, spec: SourceSpec, *, pipeline: str, client: httpx.Client) -> None:
        self._spec = spec
        self._pipeline = pipeline
        self._client = client

    def units(self) -> Iterator[UnitSpec]:
        """offset=0, size, 2*size, … 무한 열거. unit_key = "offset={n}"."""
        raise NotImplementedError("M1 Task 6 — docs/plans/2026-07-08-m1-core-foundation.md")

    def fetch(self, unit: UnitSpec) -> FetchResult:
        """GET → classify_http_status로 예외 매핑 → RecordBatch.

        len(rows) < size 이면 exhausted=True. 빈 응답은 batch=None.
        """
        raise NotImplementedError("M1 Task 6 — docs/plans/2026-07-08-m1-core-foundation.md")
