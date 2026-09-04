"""REST 소스 — M1은 offset 페이지네이션 1종.

구현: docs/plans/2026-07-08-m1-core-foundation.md Task 6
M2 확장: page/cursor 모드, encoding, rate limiter, AuthProvider 연동 (docs/01-scope.md M2)
"""

import itertools
import json
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pyarrow as pa

from arsenal_core.errors import FatalError, classify_http_status
from arsenal_core.ratelimit import Clock, TokenBucket
from arsenal_core.spec.models import RestSourceSpec
from arsenal_core.state import SOURCE_EXHAUSTED, UnitSpec
from arsenal_core.timewindow import (
    canonical_timestamp,
    format_timestamp,
    iter_windows,
    parse_duration,
    parse_timestamp,
)
from pugio.auth import AuthProvider
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


def _merge_static_query(url: str, dynamic: dict[str, str | int]) -> dict[str, str | int]:
    """url 자체에 박힌 정적 쿼리 파라미터(예: 공공데이터포털의 serviceKey — API 키를
    쿼리 파라미터로 실어야 하는 API에는 그 외 표현 수단이 없다)와 페이지네이션
    파라미터를 병합한다.

    httpx.Client.get(url, params=X)는 X가 주어지면 url의 기존 쿼리 문자열을
    "병합"이 아니라 통째로 대체해버린다 — 이 병합 없이는 url에 심어둔 API 키가
    조용히 요청에서 빠진 채 나간다 (M2-H 실전 API 검증에서 발견).
    """
    static = dict(httpx.URL(url).params)
    return {**static, **dynamic}


class RestSource:
    def __init__(
        self,
        spec: RestSourceSpec,
        *,
        pipeline: str,
        client: httpx.Client,
        initial_cursor: str | None = None,
        initial_watermark: str | None = None,
        clock: Clock | None = None,
        now: Callable[[], datetime] | None = None,
        auth: AuthProvider | None = None,
    ) -> None:
        self._spec = spec
        self._pipeline = pipeline
        self._client = client
        # cursor/link 공용 상태 — fetch()가 매 호출 후 갱신한다 (units()는 lazy하게 읽는다).
        self._pending_cursor = initial_cursor
        self._cursor_exhausted = False
        # 증분(시간 창) 상태. 워터마크는 "마지막으로 완주한 창의 끝"이고, 없으면
        # spec.incremental.start가 첫 실행의 시작점이다.
        self._watermark = initial_watermark
        # 현재 창의 페이지네이션이 끝났는지 — _cursor_exhausted와 같은 역할이되
        # 창 하나에만 적용된다. fetch()가 세우고 _incremental_units()가 읽는다.
        self._window_exhausted = False
        self._now = now or (lambda: datetime.now(UTC))
        # M2-C: rate_limit이 없으면 완전히 비활성 — 기존 호출부(clock/rate_limit
        # 미지정)를 깨지 않기 위해 기본값을 안전하게 None으로 둔다.
        self._bucket = (
            TokenBucket(spec.rate_limit.rps, clock=clock) if spec.rate_limit is not None else None
        )
        # M2-D: auth가 없으면 spec.headers만 사용 — 기존 호출부(auth 미지정)를 깨지 않는다.
        self._auth = auth

    def _request_headers(self) -> dict[str, str]:
        """spec.headers + auth.headers() 병합. auth가 우선순위상 뒤에 와서 같은
        키(예: Authorization)를 덮어쓸 수 있게 한다."""
        if self._auth is None:
            return self._spec.headers
        return {**self._spec.headers, **self._auth.headers()}

    def units(self) -> Iterator[UnitSpec]:
        """모드별로 unit을 lazy하게 열거한다 (offset/page/cursor/link).

        `incremental`이 선언돼 있으면 그 위에 시간 창 층이 한 겹 더 얹힌다 —
        창 하나마다 페이지네이션을 완주하고 다음 창으로 넘어간다.
        """
        if self._spec.incremental is not None:
            yield from self._incremental_units()
            return
        mode = self._spec.pagination.mode
        if mode == "page":
            yield from self._page_units()
        elif mode in ("cursor", "link"):
            yield from self._cursor_units()
        else:
            yield from self._offset_units()

    def _offset_units(self) -> Iterator[UnitSpec]:
        """offset=0, size, 2*size, … 무한 열거. unit_key = "offset={n}:limit={size}".

        크기가 unit_key에 포함돼야 한다 — 같은 offset이라도 크기가 달라지면 가리키는
        행 범위가 달라지기 때문이다. 빠뜨리면 크기를 바꾼 뒤 재실행할 때 이미 done인
        유닛으로 스킵되어 상태와 데이터가 조용히 어긋난다 (docs/02-architecture.md).
        """
        size = self._spec.pagination.size
        for offset in itertools.count(0, size):
            yield UnitSpec.create(
                pipeline=self._pipeline,
                source=self._spec.url,
                unit_key=f"offset={offset}:limit={size}",
                payload={"offset": offset, "limit": size},
            )

    def _page_units(self) -> Iterator[UnitSpec]:
        """start_page부터 1씩 증가. unit_key = "page={n}:per_page={size}" (위와 같은 이유)."""
        size = self._spec.pagination.size
        for n in itertools.count(self._spec.pagination.start_page):
            yield UnitSpec.create(
                pipeline=self._pipeline,
                source=self._spec.url,
                unit_key=f"page={n}:per_page={size}",
                payload={"page": n, "per_page": size},
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

    def _incremental_units(self) -> Iterator[UnitSpec]:
        """워터마크부터 `지금 - lag`까지의 완결된 창을 순서대로, 창마다 페이지 완주.

        완료된 창은 워터마크가 이미 지나갔으므로 다시 열거되지 않는다 — 재실행
        비용이 "진행 중이던 창 하나"로 유지된다 (재개 비용 ≤ 청크 1개, 설계 원칙 2).
        열거할 창이 없으면(=아직 한 창도 완결되지 않았으면) unit을 하나도 내지 않아
        재실행이 깨끗한 no-op이 된다.
        """
        inc = self._spec.incremental
        if inc is None:  # pragma: no cover - units()가 이미 분기했다
            raise FatalError("incremental units requested without an incremental spec")
        start = parse_timestamp(self._watermark) if self._watermark else parse_timestamp(inc.start)
        end = self._now() - parse_duration(inc.lag)
        windows = list(iter_windows(start, end, parse_duration(inc.window)))
        for index, (since, until) in enumerate(windows):
            self._window_exhausted = False
            last_window = index == len(windows) - 1
            for unit in self._window_page_units(since, until, last_window=last_window):
                yield unit
                # fetch()가 이 창의 마지막 페이지였다고 알려주면 다음 창으로 넘어간다.
                # 이미 done이라 러너가 skip한 unit은 fetch()를 부르지 않으므로 플래그가
                # 그대로 False다 — 그래서 중단됐던 창은 남은 페이지부터 이어서 받는다.
                if self._window_exhausted:
                    break

    def _window_page_units(
        self, since: datetime, until: datetime, *, last_window: bool
    ) -> Iterator[UnitSpec]:
        """창 하나 안에서의 페이지 열거. unit_key에 창 경계를 접두어로 넣는다.

        경계를 unit_key에 넣어야 창마다 다른 unit이 된다 — 빼면 창이 달라져도 같은
        `offset=0` unit이라 두 번째 창부터 통째로 skip된다.

        전송 표기(`format`)가 아니라 표준형(UTC ISO)으로 키를 만든다: `format`을
        바꿔도 가리키는 시간 구간은 같으므로 unit ID가 흔들리면 안 된다.
        """
        inc = self._spec.incremental
        if inc is None:  # pragma: no cover
            raise FatalError("incremental units requested without an incremental spec")
        p = self._spec.pagination
        size = p.size
        prefix = f"since={canonical_timestamp(since)}:until={canonical_timestamp(until)}"
        window: dict[str, Any] = {
            "since": format_timestamp(since, inc.format),
            "until": format_timestamp(until, inc.format),
            # 이 창을 완주했을 때 워터마크가 될 값 — 다음 실행의 시작점.
            "watermark": canonical_timestamp(until),
            "last_window": last_window,
        }
        if p.mode == "page":
            for n in itertools.count(p.start_page):
                yield UnitSpec.create(
                    pipeline=self._pipeline,
                    source=self._spec.url,
                    unit_key=f"{prefix}:page={n}:per_page={size}",
                    payload={"page": n, "per_page": size, **window},
                )
        else:
            for offset in itertools.count(0, size):
                yield UnitSpec.create(
                    pipeline=self._pipeline,
                    source=self._spec.url,
                    unit_key=f"{prefix}:offset={offset}:limit={size}",
                    payload={"offset": offset, "limit": size, **window},
                )

    def _incremental_params(self, unit: UnitSpec) -> dict[str, str | int]:
        """창 경계를 요청 파라미터로. `until_param`이 없으면 시작 경계만 보낸다."""
        inc = self._spec.incremental
        if inc is None:  # pragma: no cover
            raise FatalError("incremental params requested without an incremental spec")
        params: dict[str, str | int] = {inc.since_param: cast(str, unit.payload["since"])}
        if inc.until_param is not None:
            params[inc.until_param] = cast(str, unit.payload["until"])
        return params

    def _request_params(self, unit: UnitSpec) -> dict[str, str | int]:
        """모드별 쿼리 파라미터. link 모드는 fetch()에서 별도 처리 (URL 자체가 다음 페이지)."""
        p = self._spec.pagination
        window = self._incremental_params(unit) if self._spec.incremental is not None else {}
        if p.mode == "page":
            return {p.param: unit.payload["page"], p.size_param: p.size, **window}
        if p.mode == "cursor":
            cur = unit.payload["cursor"]
            if cur is None:
                return {p.size_param: p.size}
            if p.cursor_param is None:
                # model_validator가 cursor 모드에서 보장하지만, assert는 python -O로
                # 스트립되고 bandit B101이 지적하므로 명시적으로 체크한다.
                raise FatalError("cursor mode requires cursor_param")
            return {p.cursor_param: cur, p.size_param: p.size}
        return {p.param: unit.payload["offset"], p.size_param: p.size, **window}

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
        if self._spec.method == "POST":
            # M2-H: Notion 검색처럼 페이지네이션 파라미터를 쿼리가 아니라 JSON
            # 바디로 실어야 하는 API — 정적 body + _request_params()를 병합한다.
            json_body = {**(self._spec.body or {}), **self._request_params(unit)}
            resp = self._client.post(
                self._spec.url,
                json=json_body,
                headers=self._request_headers(),
            )
        else:
            resp = self._client.get(
                self._spec.url,
                params=_merge_static_query(self._spec.url, self._request_params(unit)),
                headers=self._request_headers(),
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
            # 지키도록 문자열로 강제 변환한다. Slack처럼 "더 없음"을 null이 아니라
            # 빈 문자열로 신호하는 API가 실전에 있다(M2-H 실전 API 검증에서 발견) —
            # `""`만 None과 동일하게 취급하고, 숫자 0처럼 falsy이지만 유효한 커서는
            # (str(0) == "0"이 non-empty이므로) 그대로 살려 계속 진행시킨다.
            next_cursor = str(raw_cursor) if raw_cursor not in (None, "") else None
            self._pending_cursor = next_cursor
            self._cursor_exhausted = next_cursor is None
            return FetchResult(batch=batch, exhausted=next_cursor is None, next_cursor=next_cursor)
        short_page = len(rows) < p.size
        if self._spec.incremental is not None:
            # 짧은 페이지 = 이 **창**의 끝. 마지막 창일 때만 run 전체의 끝이다 —
            # 중간 창에서 exhausted=True를 돌려주면 러너가 거기서 break해 나머지
            # 창을 영영 수집하지 못한다.
            self._window_exhausted = short_page
            watermark = cast(str, unit.payload["watermark"]) if short_page else None
            last_window = cast(bool, unit.payload["last_window"])
            return FetchResult(
                batch=batch,
                exhausted=short_page and last_window,
                next_cursor=watermark,
            )
        return FetchResult(batch=batch, exhausted=short_page)

    def _fetch_link(self, unit: UnitSpec) -> FetchResult:
        """B7: Link 헤더(rel="next")의 절대 URL을 그대로 GET한다."""
        p = self._spec.pagination
        if self._bucket is not None:
            self._bucket.acquire()
        cur = unit.payload["cursor"]
        if cur is not None:
            resp = self._client.get(cur, headers=self._request_headers())
        else:
            resp = self._client.get(
                self._spec.url,
                params=_merge_static_query(self._spec.url, {p.size_param: p.size}),
                headers=self._request_headers(),
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
        if resp.status_code == 401:
            # 401 본문은 state.db에 mark_failed(str(e))로 영속된다 — 인증 서버가
            # 요청 헤더/토큰을 에코백하는 경우가 있어 본문을 그대로 실으면 시크릿이
            # state 파일에 새어나간다. 러너는 갱신 신호로만 쓰므로 상태코드+URL이면 충분하다.
            raise exc_type(f"GET {resp.url} -> {resp.status_code}")
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
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            # 비-JSON 200 응답(HTML 에러 페이지 등)은 재시도해도 나아지지 않는다 — Fatal로 분류.
            raise FatalError(f"response from {resp.url} is not valid JSON: {e}") from e

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
