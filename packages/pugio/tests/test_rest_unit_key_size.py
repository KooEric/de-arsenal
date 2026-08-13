"""offset/page unit_key는 페이지 크기를 포함해야 한다.

크기를 바꾸면 같은 offset이라도 **가리키는 행 범위가 달라진다**. unit_key에 크기가
없으면 unit_id가 그대로라 이미 `done`인 유닛으로 스킵되고, 상태와 실제 데이터가
조용히 어긋난다. 설계 문서(docs/02-architecture.md)는 처음부터 `offset=200:limit=100`
형태를 명시하고 있었다 — 구현이 드리프트했던 것.
"""

import httpx

from arsenal_core.spec.models import PaginationSpec, RestSourceSpec
from pugio.sources.rest import RestSource


def _unit_keys(mode: str, size: int, count: int = 2) -> list[str]:
    spec = RestSourceSpec(
        type="rest",
        url="https://api.example.com/items",
        pagination=PaginationSpec(mode=mode, size=size),  # pyright: ignore[reportArgumentType]
    )
    source = RestSource(spec, pipeline="p", client=httpx.Client())
    keys: list[str] = []
    for unit in source.units():
        keys.append(unit.unit_key)
        if len(keys) == count:
            break
    return keys


def test_offset_unit_key_includes_size() -> None:
    assert _unit_keys("offset", 100)[0] == "offset=0:limit=100"


def test_page_unit_key_includes_size() -> None:
    assert _unit_keys("page", 50)[0] == "page=1:per_page=50"


def test_changing_offset_size_changes_unit_id() -> None:
    """크기가 다르면 다른 작업 단위 — 같은 ID를 재사용하면 안 된다."""
    spec_a = RestSourceSpec(
        type="rest",
        url="https://api.example.com/items",
        pagination=PaginationSpec(mode="offset", size=100),
    )
    spec_b = spec_a.model_copy(update={"pagination": PaginationSpec(mode="offset", size=50)})
    first_a = next(iter(RestSource(spec_a, pipeline="p", client=httpx.Client()).units()))
    first_b = next(iter(RestSource(spec_b, pipeline="p", client=httpx.Client()).units()))
    assert first_a.unit_id != first_b.unit_id


def test_changing_page_size_changes_unit_id() -> None:
    spec_a = RestSourceSpec(
        type="rest",
        url="https://api.example.com/items",
        pagination=PaginationSpec(mode="page", size=100),
    )
    spec_b = spec_a.model_copy(update={"pagination": PaginationSpec(mode="page", size=25)})
    first_a = next(iter(RestSource(spec_a, pipeline="p", client=httpx.Client()).units()))
    first_b = next(iter(RestSource(spec_b, pipeline="p", client=httpx.Client()).units()))
    assert first_a.unit_id != first_b.unit_id
