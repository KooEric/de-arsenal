"""M2-H: RestSourceSpec.method + body — Notion 검색처럼 페이지네이션 파라미터를
쿼리가 아니라 POST JSON 바디로 실어 보내야 하는 API를 위한 최소 확장.

GET이 기본값이라 기존 스펙/테스트는 전혀 영향받지 않는다 (하위 호환).
"""

import json

import httpx
import respx

from arsenal_core.spec.models import PaginationSpec, RestSourceSpec
from pugio.sources.rest import RestSource


def test_default_method_is_get_and_body_is_none() -> None:
    """기존 YAML(method 필드 없음)은 method="GET", body=None으로 파싱된다."""
    spec = RestSourceSpec(
        type="rest",
        url="https://api.test/items",
        pagination=PaginationSpec(mode="offset", size=2),
    )
    assert spec.method == "GET"
    assert spec.body is None


@respx.mock
def test_get_path_unaffected_by_new_fields() -> None:
    """method/body 필드 추가가 기존 GET 경로에 회귀를 일으키지 않는다."""
    respx.get("https://api.test/items").respond(json=[{"id": 1}, {"id": 2}])
    spec = RestSourceSpec(
        type="rest",
        url="https://api.test/items",
        pagination=PaginationSpec(mode="offset", size=2),
    )
    src = RestSource(spec, pipeline="p", client=httpx.Client())
    result = src.fetch(next(iter(src.units())))
    assert result.batch is not None and result.batch.num_rows == 2


@respx.mock
def test_post_method_sends_json_body_with_pagination_fields() -> None:
    """method: POST일 때 static body + 페이지네이션 파라미터(cursor/size)가 쿼리
    문자열이 아니라 JSON 바디에 병합되어 실린다 (Notion 검색 API 계약)."""
    route = respx.post("https://api.test/search").respond(
        json={"results": [{"id": 1}], "next_cursor": None}
    )
    spec = RestSourceSpec(
        type="rest",
        url="https://api.test/search",
        method="POST",
        body={"filter": {"value": "page", "property": "object"}},
        pagination=PaginationSpec(
            mode="cursor",
            size_param="page_size",
            size=100,
            cursor_param="start_cursor",
            cursor_path="next_cursor",
            record_path="results",
        ),
    )
    src = RestSource(spec, pipeline="p", client=httpx.Client())
    unit = next(iter(src.units()))
    result = src.fetch(unit)

    assert route.calls.last.request.method == "POST"
    sent = json.loads(route.calls.last.request.content)
    # 첫 unit의 cursor는 None → cursor_param은 실리지 않고 size_param만 병합된다.
    assert sent == {"filter": {"value": "page", "property": "object"}, "page_size": 100}
    assert result.batch is not None and result.batch.num_rows == 1
    assert result.exhausted is True


@respx.mock
def test_post_method_carries_cursor_param_on_subsequent_page() -> None:
    """두 번째 페이지부터는 cursor_param(start_cursor)도 JSON 바디에 실린다."""
    route = respx.post("https://api.test/search").respond(
        json={"results": [{"id": 2}], "next_cursor": None}
    )
    spec = RestSourceSpec(
        type="rest",
        url="https://api.test/search",
        method="POST",
        pagination=PaginationSpec(
            mode="cursor",
            size_param="page_size",
            size=100,
            cursor_param="start_cursor",
            cursor_path="next_cursor",
            record_path="results",
        ),
    )
    src = RestSource(spec, pipeline="p", client=httpx.Client(), initial_cursor="abc123")
    unit = next(iter(src.units()))
    src.fetch(unit)
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"page_size": 100, "start_cursor": "abc123"}


@respx.mock
def test_post_method_merges_auth_headers() -> None:
    """POST 경로도 GET과 동일하게 auth 헤더를 병합해야 한다."""

    class StubAuth:
        def headers(self) -> dict[str, str]:
            return {"Authorization": "Bearer tok-post"}

        def refresh(self) -> None:  # pragma: no cover - not exercised here
            pass

    route = respx.post("https://api.test/search").respond(json={"results": [], "next_cursor": None})
    spec = RestSourceSpec(
        type="rest",
        url="https://api.test/search",
        method="POST",
        pagination=PaginationSpec(
            mode="cursor",
            size_param="page_size",
            size=10,
            cursor_param="start_cursor",
            cursor_path="next_cursor",
            record_path="results",
        ),
    )
    src = RestSource(spec, pipeline="p", client=httpx.Client(), auth=StubAuth())
    src.fetch(next(iter(src.units())))
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok-post"
