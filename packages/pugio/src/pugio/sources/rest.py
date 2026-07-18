"""REST 소스 — M1은 offset 페이지네이션 1종.

구현: docs/plans/2026-07-08-m1-core-foundation.md Task 6
M2 확장: page/cursor 모드, encoding, rate limiter, AuthProvider 연동 (docs/01-scope.md M2)
"""

import itertools
from collections.abc import Iterator

import httpx
import pyarrow as pa

from arsenal_core.errors import classify_http_status
from arsenal_core.spec.models import RestSourceSpec
from arsenal_core.state import UnitSpec
from pugio.sources.base import FetchResult


class RestSource:
    def __init__(self, spec: RestSourceSpec, *, pipeline: str, client: httpx.Client) -> None:
        self._spec = spec
        self._pipeline = pipeline
        self._client = client

    def units(self) -> Iterator[UnitSpec]:
        """모드별로 unit을 lazy하게 열거한다 (offset/page/cursor/link)."""
        mode = self._spec.pagination.mode
        if mode == "page":
            yield from self._page_units()
        else:
            yield from self._offset_units()

    def _offset_units(self) -> Iterator[UnitSpec]:
        """offset=0, size, 2*size, … 무한 열거. unit_key = "offset={n}"."""
        size = self._spec.pagination.size
        for offset in itertools.count(0, size):
            yield UnitSpec.create(
                pipeline=self._pipeline,
                source=self._spec.url,
                unit_key=f"offset={offset}",
                payload={"offset": offset, "limit": size},
            )

    def _page_units(self) -> Iterator[UnitSpec]:
        """start_page부터 1씩 증가. unit_key = "page={n}"."""
        for n in itertools.count(self._spec.pagination.start_page):
            yield UnitSpec.create(
                pipeline=self._pipeline,
                source=self._spec.url,
                unit_key=f"page={n}",
                payload={"page": n},
            )

    def _request_params(self, unit: UnitSpec) -> dict[str, str | int]:
        """모드별 쿼리 파라미터. offset/page 공용 — cursor/link는 B4/B7에서 분기."""
        p = self._spec.pagination
        if p.mode == "page":
            return {p.param: unit.payload["page"], p.size_param: p.size}
        return {p.param: unit.payload["offset"], p.size_param: p.size}

    def fetch(self, unit: UnitSpec) -> FetchResult:
        """GET → classify_http_status로 예외 매핑 → RecordBatch.

        len(rows) < size 이면 exhausted=True. 빈 응답은 batch=None.
        """
        p = self._spec.pagination
        resp = self._client.get(
            self._spec.url,
            params=self._request_params(unit),
            headers=self._spec.headers,
        )
        exc_type = classify_http_status(resp.status_code)
        if exc_type is not None:
            raise exc_type(f"GET {self._spec.url} -> {resp.status_code}: {resp.text[:200]}")
        rows: list[dict[str, object]] = resp.json()
        exhausted = len(rows) < p.size
        batch = pa.RecordBatch.from_pylist(rows) if rows else None
        return FetchResult(batch=batch, exhausted=exhausted)

    def close(self) -> None:
        """httpx.Client의 커넥션 풀을 닫는다. run_pipeline이 finally에서 호출한다."""
        self._client.close()
