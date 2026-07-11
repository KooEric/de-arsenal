import httpx
import pytest
import respx

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.spec.models import PaginationSpec, SourceSpec
from pugio.sources.rest import RestSource

SPEC = SourceSpec(
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
