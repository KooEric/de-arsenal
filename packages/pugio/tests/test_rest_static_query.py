"""M2-H: url 자체에 박힌 정적 쿼리 파라미터(예: 공공데이터포털의 serviceKey)가
페이지네이션 파라미터와 함께 살아남아야 한다.

httpx.Client.get(url, params=X)는 X를 넘기면 url의 기존 쿼리 문자열을 통째로
"대체"해버린다(병합이 아니다) — RestSource가 pagination 파라미터를 항상
`params=`로 넘기므로, 이 병합 없이는 "API 키를 url에 심는다"는 흔한 패턴(쿼리
파라미터 인증)이 실제 요청에서는 키가 빠진 채 나간다. 실전 API 검증(data-go-kr,
slack)에서 발견된 진짜 버그 — 계약 테스트가 아니라 여기서 직접 재현/고정한다.
"""

import httpx
import respx

from arsenal_core.spec.models import PaginationSpec, RestSourceSpec
from pugio.sources.rest import RestSource


@respx.mock
def test_static_query_param_in_url_survives_offset_pagination() -> None:
    spec = RestSourceSpec(
        type="rest",
        url="https://api.test/items?apikey=secret123",
        pagination=PaginationSpec(mode="offset", size=2),
    )
    route = respx.get("https://api.test/items").respond(json=[{"id": 1}])
    src = RestSource(spec, pipeline="p", client=httpx.Client())
    src.fetch(next(iter(src.units())))

    sent_params = dict(route.calls.last.request.url.params)
    assert sent_params["apikey"] == "secret123"
    assert sent_params["offset"] == "0"
    assert sent_params["limit"] == "2"


@respx.mock
def test_static_query_param_in_url_survives_page_pagination() -> None:
    spec = RestSourceSpec(
        type="rest",
        url="https://api.test/items?apikey=secret123",
        pagination=PaginationSpec(mode="page", param="page", size=2, start_page=1),
    )
    route = respx.get("https://api.test/items").respond(json=[{"id": 1}])
    src = RestSource(spec, pipeline="p", client=httpx.Client())
    src.fetch(next(iter(src.units())))

    sent_params = dict(route.calls.last.request.url.params)
    assert sent_params["apikey"] == "secret123"
    assert sent_params["page"] == "1"


@respx.mock
def test_static_query_param_in_url_survives_link_mode_first_request() -> None:
    """link 모드의 첫 요청(cursor 없음)도 size_param을 params=로 넘기므로 동일한
    함정이 있다 — 후속 요청(Link 헤더의 절대 URL)은 params 없이 그대로 GET하므로
    영향 없다."""
    spec = RestSourceSpec(
        type="rest",
        url="https://api.test/link-items?apikey=secret123",
        pagination=PaginationSpec(mode="link", size_param="per_page", size=1),
    )
    route = respx.get("https://api.test/link-items").respond(json=[{"id": 1}])
    src = RestSource(spec, pipeline="p", client=httpx.Client())
    src.fetch(next(iter(src.units())))

    sent_params = dict(route.calls.last.request.url.params)
    assert sent_params["apikey"] == "secret123"
    assert sent_params["per_page"] == "1"


@respx.mock
def test_no_static_query_param_is_unaffected() -> None:
    """회귀 방지: url에 쿼리가 전혀 없는 기존 스펙은 그대로 동작한다."""
    spec = RestSourceSpec(
        type="rest",
        url="https://api.test/plain-items",
        pagination=PaginationSpec(mode="offset", size=2),
    )
    route = respx.get("https://api.test/plain-items").respond(json=[{"id": 1}])
    src = RestSource(spec, pipeline="p", client=httpx.Client())
    src.fetch(next(iter(src.units())))
    assert dict(route.calls.last.request.url.params) == {"offset": "0", "limit": "2"}
