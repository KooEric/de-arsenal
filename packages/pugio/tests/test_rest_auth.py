"""M2-D: RestSource가 AuthProvider.headers()를 요청에 병합하는지 검증.

두 요청 경로(offset/page/cursor 공용 fetch()와 link 전용 _fetch_link()) 모두에서
Authorization 헤더가 실려 나가야 한다.
"""

import httpx
import respx

from arsenal_core.spec.models import PaginationSpec, RestSourceSpec
from pugio.sources.rest import RestSource


class StubAuth:
    def __init__(self, token: str) -> None:
        self._token = token
        self.refresh_count = 0

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def refresh(self) -> None:
        self.refresh_count += 1


@respx.mock
def test_auth_headers_merged_into_request() -> None:
    spec = RestSourceSpec(
        type="rest",
        url="https://api.test/auth-items",
        pagination=PaginationSpec(mode="offset", size=2),
    )
    route = respx.get("https://api.test/auth-items").respond(json=[{"id": 1}])
    src = RestSource(spec, pipeline="p", client=httpx.Client(), auth=StubAuth("tok-x"))
    unit = next(iter(src.units()))
    src.fetch(unit)
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok-x"


@respx.mock
def test_auth_headers_merged_into_link_request() -> None:
    spec = RestSourceSpec(
        type="rest",
        url="https://api.test/auth-link-items",
        pagination=PaginationSpec(mode="link", size=2),
    )
    route = respx.get("https://api.test/auth-link-items").respond(json=[{"id": 1}])
    src = RestSource(spec, pipeline="p", client=httpx.Client(), auth=StubAuth("tok-y"))
    unit = next(iter(src.units()))
    src.fetch(unit)
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok-y"


@respx.mock
def test_no_auth_means_no_authorization_header() -> None:
    spec = RestSourceSpec(
        type="rest",
        url="https://api.test/no-auth-items",
        pagination=PaginationSpec(mode="offset", size=2),
    )
    route = respx.get("https://api.test/no-auth-items").respond(json=[{"id": 1}])
    src = RestSource(spec, pipeline="p", client=httpx.Client())
    unit = next(iter(src.units()))
    src.fetch(unit)
    assert "Authorization" not in route.calls.last.request.headers
