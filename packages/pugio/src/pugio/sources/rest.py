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
        """offset=0, size, 2*size, … 무한 열거. unit_key = "offset={n}"."""
        size = self._spec.pagination.size
        for offset in itertools.count(0, size):
            yield UnitSpec.create(
                pipeline=self._pipeline,
                source=self._spec.url,
                unit_key=f"offset={offset}",
                payload={"offset": offset, "limit": size},
            )

    def fetch(self, unit: UnitSpec) -> FetchResult:
        """GET → classify_http_status로 예외 매핑 → RecordBatch.

        len(rows) < size 이면 exhausted=True. 빈 응답은 batch=None.
        """
        p = self._spec.pagination
        resp = self._client.get(
            self._spec.url,
            params={p.param: unit.payload["offset"], p.size_param: unit.payload["limit"]},
            headers=self._spec.headers,
        )
        exc_type = classify_http_status(resp.status_code)
        if exc_type is not None:
            raise exc_type(f"GET {self._spec.url} -> {resp.status_code}: {resp.text[:200]}")
        rows: list[dict[str, object]] = resp.json()
        exhausted = len(rows) < p.size
        batch = pa.RecordBatch.from_pylist(rows) if rows else None
        return FetchResult(batch=batch, exhausted=exhausted)
