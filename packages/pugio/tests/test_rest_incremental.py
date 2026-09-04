"""REST 증분 수집(시간 창) — "지난 실행 이후 새 데이터만"의 계약을 고정한다.

핵심 불변식 네 가지:
  1. unit_key가 창 경계 + 페이지를 모두 담는다 (창마다 다른 작업 단위).
  2. 창 중간 페이지는 exhausted를 올리지 않는다 (올리면 러너가 남은 창을 버린다).
  3. 워터마크는 창을 완주한 페이지에서만 전진한다 (중간에 죽으면 그 창을 다시 받는다).
  4. 완료된 창은 다시 열거되지 않는다 (재실행 비용 = 진행 중이던 창 하나).
"""

from datetime import UTC, datetime
from itertools import islice

import httpx
import respx

from arsenal_core.spec.models import PaginationSpec, RestIncrementalSpec, RestSourceSpec
from pugio.sources.rest import RestSource

NOW = datetime(2026, 3, 4, 12, 0, 0, tzinfo=UTC)
URL = "https://api.test/orders"


def make_spec(
    *,
    mode: str = "offset",
    size: int = 2,
    start: str = "2026-03-01T00:00:00Z",
    window: str = "1d",
    lag: str = "0s",
    until_param: str | None = "updated_before",
    fmt: str = "iso8601",
) -> RestSourceSpec:
    return RestSourceSpec(
        type="rest",
        url=URL,
        pagination=PaginationSpec(mode=mode, size=size),  # pyright: ignore[reportArgumentType]
        incremental=RestIncrementalSpec(
            since_param="updated_after",
            until_param=until_param,
            start=start,
            window=window,
            lag=lag,
            format=fmt,  # pyright: ignore[reportArgumentType]
        ),
    )


def make_source(spec: RestSourceSpec | None = None, watermark: str | None = None) -> RestSource:
    return RestSource(
        spec or make_spec(),
        pipeline="p",
        client=httpx.Client(),
        initial_watermark=watermark,
        now=lambda: NOW,
    )


def drive(src: RestSource, *, limit: int = 50) -> list[str]:
    """러너처럼 units()를 fetch로 밀어 창을 넘긴다.

    units()만으로는 창 안에서 무한하다 — 창의 끝은 응답(짧은 페이지)만이 알려주고,
    그 신호는 fetch()를 통해서만 들어온다. 그래서 여기서도 러너와 같은 루프를 돈다.
    """
    keys: list[str] = []
    for unit in src.units():
        keys.append(unit.unit_key)
        if src.fetch(unit).exhausted or len(keys) >= limit:
            break
    return keys


def test_unit_key_carries_window_and_page() -> None:
    units = list(islice(make_source().units(), 3))
    assert units[0].unit_key == (
        "since=2026-03-01T00:00:00Z:until=2026-03-02T00:00:00Z:offset=0:limit=2"
    )
    assert units[1].unit_key == (
        "since=2026-03-01T00:00:00Z:until=2026-03-02T00:00:00Z:offset=2:limit=2"
    )


def test_unit_ids_are_deterministic_across_constructions() -> None:
    first = next(iter(make_source().units()))
    again = next(iter(make_source().units()))
    assert first.unit_id == again.unit_id


def test_window_boundary_uses_canonical_form_regardless_of_wire_format() -> None:
    """`format`은 전송 표기만 바꾼다 — 같은 시간 구간이면 unit ID가 같아야 한다."""
    iso = next(iter(make_source(make_spec(fmt="iso8601")).units()))
    epoch = next(iter(make_source(make_spec(fmt="epoch_s")).units()))
    assert iso.unit_id == epoch.unit_id


def test_page_mode_enumerates_pages_inside_each_window() -> None:
    unit = next(iter(make_source(make_spec(mode="page")).units()))
    assert unit.unit_key == (
        "since=2026-03-01T00:00:00Z:until=2026-03-02T00:00:00Z:page=1:per_page=2"
    )


@respx.mock
def test_request_carries_both_window_bounds() -> None:
    route = respx.get(URL).respond(json=[])
    src = make_source()
    src.fetch(next(iter(src.units())))
    params = dict(route.calls.last.request.url.params)
    assert params["updated_after"] == "2026-03-01T00:00:00Z"
    assert params["updated_before"] == "2026-03-02T00:00:00Z"
    assert params["offset"] == "0"


@respx.mock
def test_until_param_is_omitted_when_not_declared() -> None:
    route = respx.get(URL).respond(json=[])
    src = make_source(make_spec(until_param=None))
    src.fetch(next(iter(src.units())))
    params = dict(route.calls.last.request.url.params)
    assert params["updated_after"] == "2026-03-01T00:00:00Z"
    assert "updated_before" not in params


@respx.mock
def test_epoch_format_is_sent_on_the_wire() -> None:
    route = respx.get(URL).respond(json=[])
    src = make_source(make_spec(fmt="epoch_ms"))
    src.fetch(next(iter(src.units())))
    assert dict(route.calls.last.request.url.params)["updated_after"] == "1772323200000"


@respx.mock
def test_short_page_ends_the_window_but_not_the_run() -> None:
    """중간 창의 마지막 페이지가 exhausted=True면 러너가 나머지 창을 영영 못 받는다."""
    respx.get(URL).respond(json=[{"id": 1}])
    src = make_source()
    units = src.units()
    result = src.fetch(next(units))
    assert result.exhausted is False
    # 워터마크는 이 창의 끝으로 전진한다.
    assert result.next_cursor == "2026-03-02T00:00:00Z"
    # 다음에 나오는 unit은 같은 창의 다음 페이지가 아니라 다음 창의 첫 페이지다.
    assert next(units).unit_key.startswith("since=2026-03-02T00:00:00Z")


@respx.mock
def test_full_page_keeps_paginating_inside_the_same_window() -> None:
    respx.get(URL).respond(json=[{"id": 1}, {"id": 2}])
    src = make_source()
    units = src.units()
    result = src.fetch(next(units))
    assert result.exhausted is False
    assert result.next_cursor is None  # 창 중간 — 워터마크는 그대로
    assert next(units).unit_key == (
        "since=2026-03-01T00:00:00Z:until=2026-03-02T00:00:00Z:offset=2:limit=2"
    )


@respx.mock
def test_last_window_short_page_exhausts_the_run() -> None:
    respx.get(URL).respond(json=[{"id": 1}])
    keys = drive(make_source())
    assert [k.split(":offset")[0] for k in keys] == [
        "since=2026-03-01T00:00:00Z:until=2026-03-02T00:00:00Z",
        "since=2026-03-02T00:00:00Z:until=2026-03-03T00:00:00Z",
        "since=2026-03-03T00:00:00Z:until=2026-03-04T00:00:00Z",
    ]


@respx.mock
def test_watermark_replaces_start_and_skips_completed_windows() -> None:
    respx.get(URL).respond(json=[{"id": 1}])
    keys = drive(make_source(watermark="2026-03-03T00:00:00Z"))
    assert len(keys) == 1
    assert keys[0].startswith("since=2026-03-03T00:00:00Z")


def test_no_units_until_a_window_completes() -> None:
    """아직 한 창도 완결되지 않았으면 unit이 없다 — 재실행이 깨끗한 no-op이 된다."""
    assert list(make_source(watermark="2026-03-04T00:00:00Z").units()) == []


@respx.mock
def test_lag_holds_back_the_most_recent_window() -> None:
    """lag는 늦게 도착하는 데이터를 위한 안전 여유 — 최근 창을 아직 열지 않는다."""
    respx.get(URL).respond(json=[{"id": 1}])
    without = drive(make_source(make_spec(lag="0s")))
    with_lag = drive(make_source(make_spec(lag="1d")))
    assert without[-1].startswith("since=2026-03-03T00:00:00Z")
    assert with_lag[-1].startswith("since=2026-03-02T00:00:00Z")


@respx.mock
def test_smaller_windows_split_the_same_range() -> None:
    respx.get(URL).respond(json=[{"id": 1}])
    keys = drive(make_source(make_spec(window="12h")))
    # 2026-03-01T00:00 ~ NOW(2026-03-04T12:00) = 3.5일 → 12시간 창 7개.
    # 같은 범위가 1d 창에서는 3개였다(부분 창을 버리므로) — 창 크기가 신선도 상한이다.
    assert len(keys) == 7
    assert keys[-1].startswith("since=2026-03-04T00:00:00Z")
