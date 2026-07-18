import httpx
import pytest
import respx

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.spec.models import PaginationSpec, RestSourceSpec
from pugio.sources.rest import RestSource

SPEC = RestSourceSpec(
    type="rest",
    url="https://api.test/items",
    pagination=PaginationSpec(mode="offset", param="offset", size_param="limit", size=2),
)


def make_source() -> RestSource:
    return RestSource(SPEC, pipeline="p", client=httpx.Client())


def test_units_are_lazy_and_deterministic() -> None:
    from itertools import islice

    units = list(islice(make_source().units(), 2))
    assert units[0].unit_key == "offset=0"
    assert units[1].unit_key == "offset=2"
    # 같은 스펙 → 같은 unit_id (멱등의 근거)
    assert units[0].unit_id == next(iter(make_source().units())).unit_id


@respx.mock
def test_fetch_full_page_not_exhausted() -> None:
    respx.get("https://api.test/items", params={"offset": 0, "limit": 2}).respond(
        json=[{"id": 1}, {"id": 2}]
    )
    src = make_source()
    unit = next(iter(src.units()))
    result = src.fetch(unit)
    assert result.batch is not None
    assert result.batch.num_rows == 2
    assert result.exhausted is False


@respx.mock
def test_fetch_partial_page_is_exhausted() -> None:
    respx.get("https://api.test/items").respond(json=[{"id": 5}])
    result = make_source().fetch(next(iter(make_source().units())))
    assert result.batch is not None and result.batch.num_rows == 1
    assert result.exhausted is True


@respx.mock
def test_fetch_empty_page_exhausted_no_batch() -> None:
    respx.get("https://api.test/items").respond(json=[])
    result = make_source().fetch(next(iter(make_source().units())))
    assert result.batch is None
    assert result.exhausted is True


@respx.mock
@pytest.mark.parametrize(("status", "exc"), [(500, RetryableError), (404, FatalError)])
def test_http_errors_are_classified(status: int, exc: type[Exception]) -> None:
    respx.get("https://api.test/items").respond(status_code=status)
    with pytest.raises(exc):
        make_source().fetch(next(iter(make_source().units())))


def test_request_params_offset() -> None:
    """B1: offset 모드 _request_params는 {param: offset, size_param: size}를 반환한다."""
    src = make_source()
    unit = next(iter(src.units()))
    assert src._request_params(unit) == {"offset": 0, "limit": 2}


PAGE_SPEC = RestSourceSpec(
    type="rest",
    url="https://api.test/items",
    pagination=PaginationSpec(mode="page", param="page", size_param="limit", size=2, start_page=1),
)


@respx.mock
def test_page_mode_sends_incrementing_pages() -> None:
    """B2: page 모드는 start_page부터 1씩 증가하며 요청하고, 마지막 부분 페이지에서 멈춘다."""
    seen: list[int] = []

    def responder(request: httpx.Request) -> httpx.Response:
        page = int(dict(request.url.params)["page"])
        seen.append(page)
        start = PAGE_SPEC.pagination.start_page
        body = [{"id": page}, {"id": page * 100}] if page <= start + 1 else []
        return httpx.Response(200, json=body)

    respx.get("https://api.test/items").mock(side_effect=responder)
    src = RestSource(PAGE_SPEC, pipeline="p", client=httpx.Client())
    start = PAGE_SPEC.pagination.start_page
    for _, unit in zip(range(3), src.units(), strict=False):
        result = src.fetch(unit)
        if result.exhausted:
            break
    assert seen == [start, start + 1, start + 2]


def test_close_closes_underlying_httpx_client() -> None:
    """close()는 생성자에 넘긴 httpx.Client의 커넥션 풀을 닫아야 한다 (누수 방지)."""
    client = httpx.Client()
    src = RestSource(SPEC, pipeline="p", client=client)
    assert client.is_closed is False
    src.close()
    assert client.is_closed is True
