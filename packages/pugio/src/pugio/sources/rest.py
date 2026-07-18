"""REST 소스 — M1은 offset 페이지네이션 1종.

구현: docs/plans/2026-07-08-m1-core-foundation.md Task 6
M2 확장: page/cursor 모드, encoding, rate limiter, AuthProvider 연동 (docs/01-scope.md M2)
"""

import itertools
import json
import re
from collections.abc import Iterator
from typing import Any, cast

import httpx
import pyarrow as pa

from arsenal_core.errors import FatalError, classify_http_status
from arsenal_core.ratelimit import Clock, TokenBucket
from arsenal_core.spec.models import RestSourceSpec
from arsenal_core.state import SOURCE_EXHAUSTED, UnitSpec
from pugio.sources.base import FetchResult

# GitHub 스타일 `<url>; rel="next"` 만 처리한다 (P0 스코프) — RFC 8288 전체 문법
# (복수 rel, 확장 파라미터, 토큰 인용 규칙 등)은 다루지 않는다.
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
        clock: Clock | None = None,
    ) -> None:
        self._spec = spec
        self._pipeline = pipeline
        self._client = client
        # cursor/link 공용 상태 — fetch()가 매 호출 후 갱신한다 (units()는 lazy하게 읽는다).
        self._pending_cursor = initial_cursor
        self._cursor_exhausted = False
        # M2-C: rate_limit이 없으면 완전히 비활성 — 기존 호출부(clock/rate_limit
        # 미지정)를 깨지 않기 위해 기본값을 안전하게 None으로 둔다.
        self._bucket = (
            TokenBucket(spec.rate_limit.rps, clock=clock) if spec.rate_limit is not None else None
        )

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

        M2-B: _pending_cursor가 SOURCE_EXHAUSTED 센티널로 seed되면(=이전 실행에서
        이 트래버설이 이미 끝까지 완주했다는 뜻) unit을 하나도 내지 않고 즉시 return한다.
        이게 없으면: 마지막 실제 페이지의 커서가 store에 남아 있고, 재실행 시 그 unit은
        이미 done이라 runner가 skip → continue하며 fetch()를 절대 안 부르고, fetch()만
        _cursor_exhausted를 갱신하므로 같은 unit이 영원히 재생산된다 (무한루프).
        """
        while True:
            if self._pending_cursor == SOURCE_EXHAUSTED:
                return
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
            if p.cursor_param is None:
                # model_validator가 cursor 모드에서 보장하지만, assert는 python -O로
                # 스트립되고 bandit B101이 지적하므로 명시적으로 체크한다.
                raise FatalError("cursor mode requires cursor_param")
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
        if self._bucket is not None:
            self._bucket.acquire()
        resp = self._client.get(
            self._spec.url,
            params=self._request_params(unit),
            headers=self._spec.headers,
        )
        self._raise_for_status(resp)
        resp_json = self._parse_json(resp)
        rows = self._extract_rows(resp_json)
        batch = pa.RecordBatch.from_pylist(rows) if rows else None
        if p.mode == "cursor":
            if p.cursor_path is None:
                # model_validator가 cursor 모드에서 보장하지만, assert는 python -O로
                # 스트립되고 bandit B101이 지적하므로 명시적으로 체크한다.
                raise FatalError("cursor mode requires cursor_path")
            raw_cursor = _dig(resp_json, p.cursor_path)
            # API가 숫자 커서를 줄 수도 있다 — next_cursor: str | None 계약을 런타임에도
            # 지키도록 문자열로 강제 변환한다.
            next_cursor = str(raw_cursor) if raw_cursor is not None else None
            self._pending_cursor = next_cursor
            self._cursor_exhausted = next_cursor is None
            return FetchResult(batch=batch, exhausted=next_cursor is None, next_cursor=next_cursor)
        exhausted = len(rows) < p.size
        return FetchResult(batch=batch, exhausted=exhausted)

    def _fetch_link(self, unit: UnitSpec) -> FetchResult:
        """B7: Link 헤더(rel="next")의 절대 URL을 그대로 GET한다."""
        p = self._spec.pagination
        if self._bucket is not None:
            self._bucket.acquire()
        cur = unit.payload["cursor"]
        if cur is not None:
            resp = self._client.get(cur, headers=self._spec.headers)
        else:
            resp = self._client.get(
                self._spec.url, params={p.size_param: p.size}, headers=self._spec.headers
            )
        self._raise_for_status(resp)
        rows = self._extract_rows(self._parse_json(resp))
        batch = pa.RecordBatch.from_pylist(rows) if rows else None
        next_cursor = _next_from_link_header(resp)
        self._pending_cursor = next_cursor
        self._cursor_exhausted = next_cursor is None
        return FetchResult(batch=batch, exhausted=next_cursor is None, next_cursor=next_cursor)

    def _raise_for_status(self, resp: httpx.Response) -> None:
        exc_type = classify_http_status(resp.status_code)
        if exc_type is None:
            return
        # 429는 재시도 가능하지만, 다음 with_retry 시도가 서버를 또 두들기지 않도록
        # Retry-After만큼 버킷에 벌점을 먹인다 — 다음 acquire()가 자연히 그 시간을 기다린다.
        if resp.status_code == 429 and self._bucket is not None:
            retry_after = self._parse_retry_after(resp)
            if retry_after is not None:
                self._bucket.penalize(retry_after)
        raise exc_type(f"GET {resp.url} -> {resp.status_code}: {resp.text[:200]}")

    def _parse_retry_after(self, resp: httpx.Response) -> float | None:
        """Retry-After 헤더(정수 초)를 읽는다. 없거나 파싱 불가면 None (penalize 생략)."""
        raw = resp.headers.get("retry-after")
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def _parse_json(self, resp: httpx.Response) -> Any:
        """resp.json() 대신 raw body를 spec.encoding으로 직접 디코드한다.

        httpx는 Content-Type에 charset이 없으면 기본 UTF-8로 추정하므로, euc-kr 등
        비UTF-8 응답은 spec.encoding을 명시하지 않으면 이 경로에서 깨진다.
        """
        try:
            text = resp.content.decode(self._spec.encoding)
        except (UnicodeDecodeError, LookupError) as e:
            raise FatalError(
                f"cannot decode response from {resp.url} as {self._spec.encoding!r}: {e}"
            ) from e
        return json.loads(text)

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
