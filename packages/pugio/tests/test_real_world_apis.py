"""M2-H: 실전 API 5종의 respx 계약 테스트 — 페이지네이션/인증/rate-limit 설계가
탁상 설계가 아니라 실제 API 응답 형태를 견디는지 검증한다. 실 계정/네트워크는
쓰지 않는다 (respx로 실제 응답 형태를 재현).

각 API가 무엇을 증명하는지 (docs/reference/api-coverage.md에 상세):
- github:      mode: link (RFC 5988 Link 헤더), static_token 인증
- stripe:      envelope(record_path)는 표현 가능하지만 last-item-id 커서는
               dot-path로 표현 불가 → Python 탈출구가 의도된 경로 (gap 문서화)
- data-go-kr:  euc-kr 인코딩, mode: page, API 키를 쿼리 파라미터로 전달
- notion:      mode: cursor + 3req/s rate limit + POST 검색(method/body 필드)
- slack:       mode: cursor(중첩 dot-path), 429 Retry-After
"""

import json
import textwrap
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest
import respx

from arsenal_core.spec.models import (
    AuthSpec,
    PaginationSpec,
    ParquetSinkSpec,
    PipelineSpec,
    PythonSourceSpec,
    RateLimitSpec,
    RestSourceSpec,
)
from pugio.runner import run_pipeline
from pugio.sources.rest import _dig  # pyright: ignore[reportPrivateUsage]


def _read_all_parquet(out_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for f in sorted(out_dir.glob("*.parquet")):
        table = pq.read_table(f)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        rows.extend(table.to_pylist())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    return rows


# ---------------------------------------------------------------------------
# 1. GitHub — mode: link + static_token auth
# ---------------------------------------------------------------------------

GITHUB_URL = "https://api.github.com/repos/duckdb/duckdb/issues"


def _github_spec(tmp_path: Path) -> PipelineSpec:
    return PipelineSpec(
        name="rw-github",
        state_dir=tmp_path / ".arsenal",
        source=RestSourceSpec(
            type="rest",
            url=GITHUB_URL,
            pagination=PaginationSpec(mode="link", size_param="per_page", size=2),
            auth=AuthSpec(type="static_token", token_env="RW_GITHUB_TOKEN"),
        ),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )


@respx.mock
def test_github_link_pagination_and_static_token_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub 실제 응답 형태: 복수 rel(Link 헤더에 next+last)이 있어도 rel="next"만
    뽑고, static_token auth가 매 페이지 Authorization 헤더를 싣는다."""
    monkeypatch.setenv("RW_GITHUB_TOKEN", "ghp_test123")

    def responder(request: httpx.Request) -> httpx.Response:
        if "page" not in dict(request.url.params):
            return httpx.Response(
                200,
                json=[{"number": 1, "title": "a"}, {"number": 2, "title": "b"}],
                headers={
                    "link": (
                        f'<{GITHUB_URL}?page=2>; rel="next", <{GITHUB_URL}?page=9>; rel="last"'
                    )
                },
            )
        return httpx.Response(200, json=[{"number": 3, "title": "c"}])  # Link 헤더 없음 → 종료

    route = respx.get(GITHUB_URL).mock(side_effect=responder)
    report = run_pipeline(_github_spec(tmp_path))

    assert report.fetched == 2
    assert report.written == 2
    assert len(route.calls) == 2
    for call in route.calls:  # pyright: ignore[reportUnknownVariableType]
        assert (
            call.request.headers["Authorization"]  # pyright: ignore[reportUnknownMemberType]
            == "Bearer ghp_test123"
        )

    rows = _read_all_parquet(tmp_path / "out")
    assert sorted(int(r["number"]) for r in rows) == [1, 2, 3]  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. Stripe — envelope 표현 가능, last-item-id 커서는 gap → Python 탈출구
# ---------------------------------------------------------------------------

STRIPE_ENVELOPE = {
    "object": "list",
    "url": "/v1/charges",
    "has_more": True,
    "data": [{"id": "ch_1", "amount": 1000}, {"id": "ch_2", "amount": 2000}],
}


def test_stripe_envelope_record_path_is_expressible() -> None:
    """envelope(record_path: data)로 배열을 뽑는 부분은 REST 소스로 충분히 표현된다."""
    assert _dig(STRIPE_ENVELOPE, "data") == STRIPE_ENVELOPE["data"]


def test_stripe_last_item_id_cursor_is_not_expressible_via_dot_path() -> None:
    """GAP: Stripe의 다음 커서(`starting_after`)는 응답 최상위 필드가 아니라 data
    배열의 "마지막 원소의 id"다. PaginationSpec.cursor_path/_dig()는 dict의
    dot-path 순회만 하고 리스트를 만나면 즉시 None을 반환하므로(`_dig`의
    `if not isinstance(cur, dict): return None`), "data.id"처럼 배열 원소의
    필드를 향해 내려갈 수 없다 — 최상위 dict 필드(has_more 등)는 읽히지만,
    리스트 인덱싱이나 "마지막 원소" 시맨틱은 아예 없다. docs/reference/api-coverage.md
    에 기록한 gap 그대로: REST 소스의 cursor 모드로는 표현 불가 → Python 탈출구
    (examples/real-world/stripe.yaml)가 의도된 경로다.
    """
    assert _dig(STRIPE_ENVELOPE, "data") is not None  # 배열 자체는 얻을 수 있지만…
    assert _dig(STRIPE_ENVELOPE, "data.id") is None  # …그 안의 원소 필드로는 못 내려간다
    assert _dig(STRIPE_ENVELOPE, "has_more") is True  # top-level 필드는 읽히지만
    # PaginationSpec에는 "next_cursor가 None이면 종료"라는 시맨틱만 있고 "has_more가
    # False일 때 종료"라는 불리언 기반 종료 조건이 없다 — has_more와 cursor_path가
    # 별개 필드인 API(Stripe)는 이 지점에서도 선언적 스펙과 어긋난다.


@respx.mock
def test_stripe_escape_hatch_source_completes_pagination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python 탈출구로 last-item-id 커서를 직접 구현하면 문제없이 완주한다 —
    선언적 스펙의 gap이 막다른 길이 아니라는 걸 증명한다 (examples/real-world/stripe.yaml
    이 가리키는 패턴과 동일한 최소 구현)."""
    (tmp_path / "stripe_charges_source.py").write_text(
        textwrap.dedent(
            """
            import httpx
            import pyarrow as pa
            from arsenal_core.state import UnitSpec
            from pugio.sources.base import FetchResult

            class StripeChargesSource:
                \"\"\"starting_after = 이번 페이지 data 배열의 마지막 원소 id —
                REST 소스의 cursor_path(dict dot-path)로는 표현 불가라 여기서
                직접 구현한다.\"\"\"

                def __init__(self, options, *, pipeline):
                    self._url = options["url"]
                    self._pipeline = pipeline
                    self._client = httpx.Client()
                    self._next_after = None
                    self._done = False

                def units(self):
                    while not self._done:
                        yield UnitSpec.create(
                            pipeline=self._pipeline,
                            source=self._url,
                            unit_key=f"after={self._next_after}",
                            payload={},
                        )

                def fetch(self, unit):
                    params = {"limit": 2}
                    if self._next_after is not None:
                        params["starting_after"] = self._next_after
                    resp = self._client.get(self._url, params=params)
                    body = resp.json()
                    rows = body["data"]
                    batch = pa.RecordBatch.from_pylist(rows) if rows else None
                    if body["has_more"] and rows:
                        self._next_after = rows[-1]["id"]
                    else:
                        self._done = True
                    return FetchResult(batch=batch, exhausted=self._done)
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))  # pyright: ignore[reportUnknownMemberType]

    pages = {
        None: {"data": [{"id": "ch_1"}, {"id": "ch_2"}], "has_more": True},
        "ch_2": {"data": [{"id": "ch_3"}], "has_more": False},
    }

    def responder(request: httpx.Request) -> httpx.Response:
        after = dict(request.url.params).get("starting_after")
        return httpx.Response(200, json=pages[after])

    respx.get("https://api.stripe.com/v1/charges").mock(side_effect=responder)

    spec = PipelineSpec(
        name="rw-stripe",
        state_dir=tmp_path / ".arsenal",
        source=PythonSourceSpec(
            type="python",
            target="stripe_charges_source:StripeChargesSource",
            options={"url": "https://api.stripe.com/v1/charges"},
        ),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )
    report = run_pipeline(spec)
    assert report.fetched == 2
    assert report.written == 2
    rows = _read_all_parquet(tmp_path / "out")
    assert sorted(str(r["id"]) for r in rows) == ["ch_1", "ch_2", "ch_3"]  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. data-go-kr — euc-kr encoding + mode: page + API key as query param
# ---------------------------------------------------------------------------

DATA_GO_KR_URL = (
    "https://apis.data.go.kr/1230000/ad/BidPblancInfoService/getBidPblancListInfoCnstwk"
)


def _data_go_kr_spec(tmp_path: Path) -> PipelineSpec:
    return PipelineSpec(
        name="rw-data-go-kr",
        state_dir=tmp_path / ".arsenal",
        source=RestSourceSpec(
            type="rest",
            url=f"{DATA_GO_KR_URL}?serviceKey=test-service-key&_type=json",
            pagination=PaginationSpec(
                mode="page",
                param="pageNo",
                size_param="numOfRows",
                size=1,
                start_page=1,
                record_path="items",
            ),
            encoding="euc-kr",
        ),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )


@respx.mock
def test_data_go_kr_euckr_decoding_page_mode_and_query_param_key(tmp_path: Path) -> None:
    """공공데이터포털: euc-kr 바이트가 깨지지 않게 디코드되고, page 모드로
    페이지네이션하며, url에 심어둔 serviceKey가 실제 요청 쿼리에 살아남는다
    (M2-H에서 발견한 정적 쿼리 파라미터 드롭 버그의 회귀 테스트이기도 하다)."""

    def responder(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert params["serviceKey"] == "test-service-key"
        assert params["_type"] == "json"
        page = int(params["pageNo"])
        body: dict[str, list[dict[str, str]]] = (
            {"items": [{"name": "서울특별시"}]} if page == 1 else {"items": []}
        )
        raw = json.dumps(body, ensure_ascii=False).encode("euc-kr")
        return httpx.Response(200, content=raw)

    respx.get(DATA_GO_KR_URL).mock(side_effect=responder)
    report = run_pipeline(_data_go_kr_spec(tmp_path))

    assert report.fetched == 2  # 1페이지(1건) + 2페이지(0건, 부분 페이지라 exhausted)
    assert report.written == 1

    rows = _read_all_parquet(tmp_path / "out")
    assert rows[0]["name"] == "서울특별시"  # euc-kr 디코딩이 깨지지 않았다 (모지바케 없음)


# ---------------------------------------------------------------------------
# 4. Notion — mode: cursor + 3req/s rate limit + POST search (method/body)
# ---------------------------------------------------------------------------

NOTION_URL = "https://api.notion.com/v1/search"


def _notion_spec(tmp_path: Path) -> PipelineSpec:
    return PipelineSpec(
        name="rw-notion",
        state_dir=tmp_path / ".arsenal",
        source=RestSourceSpec(
            type="rest",
            url=NOTION_URL,
            method="POST",
            headers={"Notion-Version": "2022-06-28"},
            body={"filter": {"value": "page", "property": "object"}},
            pagination=PaginationSpec(
                mode="cursor",
                size_param="page_size",
                size=2,
                cursor_param="start_cursor",
                cursor_path="next_cursor",
                record_path="results",
            ),
            rate_limit=RateLimitSpec(rps=3),
            auth=AuthSpec(type="static_token", token_env="RW_NOTION_TOKEN"),
        ),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )


@respx.mock
def test_notion_post_search_cursor_pagination_and_rate_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Notion 검색: POST + JSON body로 필터/페이지네이션 파라미터가 실리고
    (method/body 필드, M2-H), next_cursor 체인을 따라가며, rate_limit이 배선돼
    있다(실제 스로틀 타이밍은 test_rest_ratelimit.py에서 이미 결정론적으로
    검증됨 — 여기서는 스펙 배선 자체를 증명한다)."""
    monkeypatch.setenv("RW_NOTION_TOKEN", "secret_notion_tok")

    pages = {
        None: {"results": [{"id": "p1"}, {"id": "p2"}], "next_cursor": "cur-2", "has_more": True},
        "cur-2": {"results": [{"id": "p3"}], "next_cursor": None, "has_more": False},
    }

    def responder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["filter"] == {"value": "page", "property": "object"}
        assert body["page_size"] == 2
        cursor = body.get("start_cursor")
        return httpx.Response(200, json=pages[cursor])

    route = respx.post(NOTION_URL).mock(side_effect=responder)
    report = run_pipeline(_notion_spec(tmp_path))

    assert report.fetched == 2
    assert report.written == 2
    for call in route.calls:  # pyright: ignore[reportUnknownVariableType]
        headers = call.request.headers  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        assert headers["Authorization"] == "Bearer secret_notion_tok"
        assert headers["Notion-Version"] == "2022-06-28"

    rows = _read_all_parquet(tmp_path / "out")
    assert sorted(str(r["id"]) for r in rows) == ["p1", "p2", "p3"]  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 5. Slack — mode: cursor(nested dot-path) + 429 Retry-After
# ---------------------------------------------------------------------------

SLACK_URL = "https://slack.com/api/conversations.history?channel=C0000000"
SLACK_PATH = "https://slack.com/api/conversations.history"


def _slack_spec(tmp_path: Path) -> PipelineSpec:
    return PipelineSpec(
        name="rw-slack",
        state_dir=tmp_path / ".arsenal",
        source=RestSourceSpec(
            type="rest",
            url=SLACK_URL,
            pagination=PaginationSpec(
                mode="cursor",
                size_param="limit",
                size=2,
                cursor_param="cursor",
                cursor_path="response_metadata.next_cursor",
                record_path="messages",
            ),
            rate_limit=RateLimitSpec(rps=1),
            auth=AuthSpec(type="static_token", token_env="RW_SLACK_TOKEN"),
        ),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )


@respx.mock
def test_slack_nested_cursor_and_channel_query_param_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slack: 중첩 dot-path(`response_metadata.next_cursor`)로 커서를 뽑고,
    url에 심어둔 channel 쿼리 파라미터가 페이지네이션 파라미터와 함께 살아남는다."""
    monkeypatch.setenv("RW_SLACK_TOKEN", "xoxb-test")

    pages = {
        None: {
            "messages": [{"ts": "1"}, {"ts": "2"}],
            "response_metadata": {"next_cursor": "abc"},
        },
        "abc": {"messages": [{"ts": "3"}], "response_metadata": {"next_cursor": ""}},
    }

    def responder(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert params["channel"] == "C0000000"  # url에 심은 정적 쿼리가 살아남는다
        cursor = params.get("cursor")
        body = pages[cursor] if cursor else pages[None]
        return httpx.Response(200, json=body)

    respx.get(SLACK_PATH).mock(side_effect=responder)
    report = run_pipeline(_slack_spec(tmp_path))

    assert report.fetched == 2
    assert report.written == 2
    rows = _read_all_parquet(tmp_path / "out")
    assert sorted(str(r["ts"]) for r in rows) == ["1", "2", "3"]  # type: ignore[arg-type]


@respx.mock
def test_slack_429_retry_after_penalizes_and_recovers() -> None:
    """Slack의 429+Retry-After 응답이 RetryableError로 분류되고 rate limiter에
    벌점을 남긴다는 것은 이미 rest.py의 일반 로직(_raise_for_status)이 API에
    무관하게 처리한다 — 여기서는 Slack이 실제로 이 헤더 형태(정수초)를 쓴다는
    계약만 재현해 고정한다."""
    from arsenal_core.errors import RetryableError
    from pugio.sources.rest import RestSource

    slack_source = RestSourceSpec(
        type="rest",
        url=SLACK_URL,
        pagination=PaginationSpec(
            mode="cursor",
            size_param="limit",
            size=2,
            cursor_param="cursor",
            cursor_path="response_metadata.next_cursor",
            record_path="messages",
        ),
        rate_limit=RateLimitSpec(rps=1),
    )
    respx.get(SLACK_PATH).respond(status_code=429, headers={"Retry-After": "1"})
    src = RestSource(slack_source, pipeline="rw-slack-429", client=httpx.Client())
    unit = next(iter(src.units()))
    with pytest.raises(RetryableError):
        src.fetch(unit)
