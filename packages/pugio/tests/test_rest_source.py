from collections.abc import Iterator

import httpx
import pytest
import respx

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.spec.models import PaginationSpec, RestSourceSpec
from arsenal_core.state import SOURCE_EXHAUSTED, UnitSpec
from pugio.sources.base import FetchResult
from pugio.sources.rest import (
    RestSource,
    _dig,  # pyright: ignore[reportPrivateUsage]
    _next_from_link_header,  # pyright: ignore[reportPrivateUsage]
)

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
    assert src._request_params(unit) == {  # pyright: ignore[reportPrivateUsage]
        "offset": 0,
        "limit": 2,
    }


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


def test_dig_nested() -> None:
    """B3: 점(.)-경로로 중첩 dict를 순회. 중간 키가 없으면 None."""
    assert _dig({"meta": {"next": "c1"}}, "meta.next") == "c1"
    assert _dig({"meta": {"next": "c1"}}, "meta.missing") is None
    assert _dig({"meta": {}}, "meta.next.deeper") is None
    assert _dig({}, "meta.next") is None


RECORD_PATH_SPEC = RestSourceSpec(
    type="rest",
    url="https://api.test/envelope",
    pagination=PaginationSpec(
        mode="offset", param="offset", size_param="limit", size=2, record_path="data"
    ),
)


@respx.mock
def test_record_path_extracts_array() -> None:
    """B3: envelope 응답에서 record_path로 지정한 배열만 레코드로 뽑는다."""
    respx.get("https://api.test/envelope").respond(
        json={"data": [{"id": 1}, {"id": 2}], "meta": {"total": 2}}
    )
    src = RestSource(RECORD_PATH_SPEC, pipeline="p", client=httpx.Client())
    result = src.fetch(next(iter(src.units())))
    assert result.batch is not None
    assert result.batch.num_rows == 2
    assert result.batch.to_pylist() == [{"id": 1}, {"id": 2}]


@respx.mock
def test_record_path_non_list_raises_fatal() -> None:
    """B3: record_path가 리스트가 아닌 값을 가리키면 FatalError."""
    respx.get("https://api.test/envelope").respond(json={"data": {"not": "a list"}})
    src = RestSource(RECORD_PATH_SPEC, pipeline="p", client=httpx.Client())
    with pytest.raises(FatalError, match="did not resolve to a list"):
        src.fetch(next(iter(src.units())))


CURSOR_SPEC = RestSourceSpec(
    type="rest",
    url="https://api.test/cursor-items",
    pagination=PaginationSpec(
        mode="cursor",
        size_param="limit",
        size=2,
        cursor_param="after",
        cursor_path="meta.next",
    ),
)


def _cursor_responder(chain: dict[str | None, tuple[list[dict[str, int]], str | None]]):
    def responder(request: httpx.Request) -> httpx.Response:
        after = dict(request.url.params).get("after")
        rows, next_cursor = chain[after]
        return httpx.Response(200, json={"data": rows, "meta": {"next": next_cursor}})

    return responder


@respx.mock
def test_cursor_mode_follows_next() -> None:
    """B4: cursor 체인 ""→c1→c2→None을 따라가고 마지막에 exhausted=True."""
    chain: dict[str | None, tuple[list[dict[str, int]], str | None]] = {
        None: ([{"id": 1}], "c1"),
        "c1": ([{"id": 2}], "c2"),
        "c2": ([{"id": 3}], None),
    }
    spec = CURSOR_SPEC.model_copy(
        update={"pagination": CURSOR_SPEC.pagination.model_copy(update={"record_path": "data"})}
    )
    respx.get("https://api.test/cursor-items").mock(side_effect=_cursor_responder(chain))
    src = RestSource(spec, pipeline="p", client=httpx.Client())
    units_iter = src.units()
    results: list[FetchResult] = []
    for _ in range(3):
        unit = next(units_iter)
        results.append(src.fetch(unit))
    assert [r.next_cursor for r in results] == ["c1", "c2", None]
    assert results[-1].exhausted is True


@respx.mock
def test_cursor_resume_from_initial() -> None:
    """B4: initial_cursor가 주어지면 첫 unit부터 그 커서로 시작한다."""
    chain: dict[str | None, tuple[list[dict[str, int]], str | None]] = {
        "c1": ([{"id": 9}], None),
    }
    spec = CURSOR_SPEC.model_copy(
        update={"pagination": CURSOR_SPEC.pagination.model_copy(update={"record_path": "data"})}
    )
    respx.get("https://api.test/cursor-items").mock(side_effect=_cursor_responder(chain))
    src = RestSource(spec, pipeline="p", client=httpx.Client(), initial_cursor="c1")
    unit = next(iter(src.units()))
    assert unit.payload["cursor"] == "c1"
    result = src.fetch(unit)
    assert result.next_cursor is None
    assert result.exhausted is True


LINK_SPEC = RestSourceSpec(
    type="rest",
    url="https://api.test/link-items",
    pagination=PaginationSpec(mode="link", size_param="per_page", size=1),
)


def test_next_from_link_header_parses_rel_next() -> None:
    """B7: GitHub 스타일 Link 헤더에서 rel="next" URL을 뽑는다. 없으면 None."""
    resp = httpx.Response(
        200,
        headers={
            "link": (
                '<https://api.test/link-items?page=2>; rel="next", '
                '<https://api.test/link-items?page=9>; rel="last"'
            )
        },
    )
    assert _next_from_link_header(resp) == "https://api.test/link-items?page=2"
    assert _next_from_link_header(httpx.Response(200)) is None


@respx.mock
def test_link_mode_follows_header() -> None:
    """B7: rel=next URL을 따라가고, Link 헤더가 없어지면 멈춘다."""
    # 경로를 다르게 둔다 — respx는 params를 명시하지 않으면 쿼리스트링을 무시하고
    # path만으로 매칭하므로, 같은 path에 다른 query만 다르면 먼저 등록된 route가 가로챈다.
    page1 = "https://api.test/link-items/p2"
    page2 = "https://api.test/link-items/p3"

    respx.get("https://api.test/link-items").respond(
        json=[{"id": 1}], headers={"link": f'<{page1}>; rel="next"'}
    )
    respx.get(page1).respond(json=[{"id": 2}], headers={"link": f'<{page2}>; rel="next"'})
    respx.get(page2).respond(json=[{"id": 3}])  # Link 헤더 없음 → 종료

    src = RestSource(LINK_SPEC, pipeline="p", client=httpx.Client())
    units_iter = src.units()
    results: list[FetchResult] = []
    for _ in range(3):
        unit = next(units_iter)
        results.append(src.fetch(unit))
    assert [r.next_cursor for r in results] == [page1, page2, None]
    assert results[-1].exhausted is True
    assert results[0].batch is not None and results[0].batch.to_pylist() == [{"id": 1}]


@respx.mock
def test_link_resume() -> None:
    """B7: initial_cursor로 넘긴 절대 URL을 첫 요청에 그대로 사용한다 (재개)."""
    resume_url = "https://api.test/link-items/p5"
    respx.get(resume_url).respond(json=[{"id": 42}])  # Link 헤더 없음 → 종료
    src = RestSource(LINK_SPEC, pipeline="p", client=httpx.Client(), initial_cursor=resume_url)
    unit = next(iter(src.units()))
    assert unit.payload["cursor"] == resume_url
    result = src.fetch(unit)
    assert result.batch is not None and result.batch.to_pylist() == [{"id": 42}]
    assert result.exhausted is True


def test_cursor_units_terminates_when_seeded_exhausted() -> None:
    """M2-B: initial_cursor=SOURCE_EXHAUSTED로 seed되면(=이전 실행에서 완료된
    트래버설) units()가 unit을 하나도 내지 않고 즉시 끝난다 — fetch()가 한 번도
    안 불려도 제너레이터 스스로 멈추는 게 재실행 무한루프 수정의 핵심이다.
    list()로 완전히 drain해도(=StopIteration까지) 끝난다는 걸 못박는다.
    """
    src = RestSource(
        CURSOR_SPEC, pipeline="p", client=httpx.Client(), initial_cursor=SOURCE_EXHAUSTED
    )
    assert list(src.units()) == []


@respx.mock
def test_cursor_units_terminates_after_natural_exhaustion() -> None:
    """cursor 체인을 실제로 끝까지 fetch하며 drain하면(마지막 next_cursor=None)
    units() 제너레이터가 StopIteration으로 스스로 멈춘다 — 재개 없이도 종료가
    보장됨을 러너와 무관하게 검증한다.
    """
    chain: dict[str | None, tuple[list[dict[str, int]], str | None]] = {
        None: ([{"id": 1}], "c1"),
        "c1": ([{"id": 2}], None),
    }
    spec = CURSOR_SPEC.model_copy(
        update={"pagination": CURSOR_SPEC.pagination.model_copy(update={"record_path": "data"})}
    )
    respx.get("https://api.test/cursor-items").mock(side_effect=_cursor_responder(chain))
    src = RestSource(spec, pipeline="p", client=httpx.Client())

    def drive() -> Iterator[UnitSpec]:
        for unit in src.units():
            yield unit
            src.fetch(unit)

    units = list(drive())
    assert len(units) == 2  # None, c1 두 유닛만 낸다 — c2는 없음 (제너레이터가 스스로 멈춤)


def test_close_closes_underlying_httpx_client() -> None:
    """close()는 생성자에 넘긴 httpx.Client의 커넥션 풀을 닫아야 한다 (누수 방지)."""
    client = httpx.Client()
    src = RestSource(SPEC, pipeline="p", client=client)
    assert client.is_closed is False
    src.close()
    assert client.is_closed is True
