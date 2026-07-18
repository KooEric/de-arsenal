"""REST 소스 — M1은 offset 페이지네이션 1종.

구현: docs/plans/2026-07-08-m1-core-foundation.md Task 6
M2 확장: page/cursor 모드, encoding, rate limiter, AuthProvider 연동 (docs/01-scope.md M2)
"""

import itertools
import re
from collections.abc import Iterator
from typing import Any, cast

import httpx
import pyarrow as pa

from arsenal_core.errors import FatalError, classify_http_status
from arsenal_core.spec.models import RestSourceSpec
from arsenal_core.state import UnitSpec
from pugio.sources.base import FetchResult

_LINK_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


def _dig(obj: Any, path: str) -> Any:
    """ "a.b.c" 같은 점(.)-경로로 중첩 dict를 순회. 중간에 키가 없으면 None."""
    cur: Any = obj
    for key in path.split("."):
        if not isinstance(cur, dict):
            return None
        d = cast(dict[str, Any], cur)
        if key not in d:
            return None
        cur = d[key]
    return cur


def _next_from_link_header(resp: httpx.Response) -> str | None:
    """GitHub 스타일 `Link: <url>; rel="next"` 헤더에서 다음 페이지 URL을 뽑는다."""
    m = _LINK_NEXT.search(resp.headers.get("link", ""))
    return m.group(1) if m else None


class RestSource:
    def __init__(
        self,
        spec: RestSourceSpec,
        *,
        pipeline: str,
        client: httpx.Client,
        initial_cursor: str | None = None,
    ) -> None:
        self._spec = spec
        self._pipeline = pipeline
        self._client = client
        # cursor/link 공용 상태 — fetch()가 매 호출 후 갱신한다 (units()는 lazy하게 읽는다).
        self._pending_cursor = initial_cursor
        self._cursor_exhausted = False

    def units(self) -> Iterator[UnitSpec]:
        """모드별로 unit을 lazy하게 열거한다 (offset/page/cursor/link)."""
        mode = self._spec.pagination.mode
        if mode == "page":
            yield from self._page_units()
        elif mode in ("cursor", "link"):
            yield from self._cursor_units()
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

    def _cursor_units(self) -> Iterator[UnitSpec]:
        """cursor/link 공용: fetch()가 갱신한 _pending_cursor를 매번 다시 읽는다.

        unit_key = "cursor={cur}" — link 모드에서도 cur은 (URL 문자열이거나) None이다.
        """
        while True:
            cur = self._pending_cursor
            yield UnitSpec.create(
                pipeline=self._pipeline,
                source=self._spec.url,
                unit_key=f"cursor={cur}",
                payload={"cursor": cur},
            )
            if self._cursor_exhausted:
                return

    def _request_params(self, unit: UnitSpec) -> dict[str, str | int]:
        """모드별 쿼리 파라미터. link 모드는 fetch()에서 별도 처리 (URL 자체가 다음 페이지)."""
        p = self._spec.pagination
        if p.mode == "page":
            return {p.param: unit.payload["page"], p.size_param: p.size}
        if p.mode == "cursor":
            cur = unit.payload["cursor"]
            if cur is None:
                return {p.size_param: p.size}
            assert p.cursor_param is not None  # model_validator가 cursor 모드에서 보장
            return {p.cursor_param: cur, p.size_param: p.size}
        return {p.param: unit.payload["offset"], p.size_param: p.size}

    def fetch(self, unit: UnitSpec) -> FetchResult:
        """GET → classify_http_status로 예외 매핑 → RecordBatch.

        offset/page: len(rows) < size 이면 exhausted=True.
        cursor/link: next_cursor가 None이면 exhausted=True (행 수와 무관).
        빈 응답은 batch=None.
        """
        p = self._spec.pagination
        if p.mode == "link":
            return self._fetch_link(unit)
        resp = self._client.get(
            self._spec.url,
            params=self._request_params(unit),
            headers=self._spec.headers,
        )
        self._raise_for_status(resp)
        resp_json = resp.json()
        rows = self._extract_rows(resp_json)
        batch = pa.RecordBatch.from_pylist(rows) if rows else None
        if p.mode == "cursor":
            assert p.cursor_path is not None  # model_validator가 cursor 모드에서 보장
            next_cursor = _dig(resp_json, p.cursor_path)
            self._pending_cursor = next_cursor
            self._cursor_exhausted = next_cursor is None
            return FetchResult(batch=batch, exhausted=next_cursor is None, next_cursor=next_cursor)
        exhausted = len(rows) < p.size
        return FetchResult(batch=batch, exhausted=exhausted)

    def _fetch_link(self, unit: UnitSpec) -> FetchResult:
        """B7: Link 헤더(rel="next")의 절대 URL을 그대로 GET한다."""
        p = self._spec.pagination
        cur = unit.payload["cursor"]
        if cur is not None:
            resp = self._client.get(cur, headers=self._spec.headers)
        else:
            resp = self._client.get(
                self._spec.url, params={p.size_param: p.size}, headers=self._spec.headers
            )
        self._raise_for_status(resp)
        rows = self._extract_rows(resp.json())
        batch = pa.RecordBatch.from_pylist(rows) if rows else None
        next_cursor = _next_from_link_header(resp)
        self._pending_cursor = next_cursor
        self._cursor_exhausted = next_cursor is None
        return FetchResult(batch=batch, exhausted=next_cursor is None, next_cursor=next_cursor)

    def _raise_for_status(self, resp: httpx.Response) -> None:
        exc_type = classify_http_status(resp.status_code)
        if exc_type is not None:
            raise exc_type(f"GET {resp.url} -> {resp.status_code}: {resp.text[:200]}")

    def _extract_rows(self, resp_json: Any) -> list[dict[str, object]]:
        """record_path가 없으면 응답 자체가 배열. 있으면 dot-path로 배열을 뽑는다."""
        p = self._spec.pagination
        if p.record_path is None:
            rows: list[dict[str, object]] = resp_json
            return rows
        dug = _dig(resp_json, p.record_path)
        if not isinstance(dug, list):
            raise FatalError(f"record_path {p.record_path!r} did not resolve to a list")
        return cast(list[dict[str, object]], dug)

    def close(self) -> None:
        """httpx.Client의 커넥션 풀을 닫는다. run_pipeline이 finally에서 호출한다."""
        self._client.close()
