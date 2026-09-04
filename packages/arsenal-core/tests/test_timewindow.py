"""시간 창 계산 — 증분 수집의 unit 경계가 결정적인지 검증한다.

경계가 흔들리면 unit ID가 흔들리고, 그러면 멱등이 깨진다. 그래서 여기 있는
테스트는 대부분 "같은 입력 → 같은 경계"와 "지금에 걸친 부분 창은 내지 않는다"를
고정하는 회귀 테스트다.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from arsenal_core.errors import FatalError
from arsenal_core.timewindow import (
    canonical_timestamp,
    format_timestamp,
    iter_windows,
    parse_duration,
    parse_timestamp,
)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("0s", 0), ("30s", 30), ("15m", 900), ("6h", 21600), ("1d", 86400), ("2w", 1209600)],
)
def test_parse_duration_accepts_every_unit(text: str, seconds: int) -> None:
    assert parse_duration(text) == timedelta(seconds=seconds)


@pytest.mark.parametrize("text", ["1 day", "", "d", "1", "-1d", "1.5h", "1y", "1D", "1w2d"])
def test_parse_duration_rejects_anything_else(text: str) -> None:
    """자연어·소수·음수·미지원 단위는 스펙 로드 시점에 걸러야 한다."""
    with pytest.raises(FatalError, match="invalid duration"):
        parse_duration(text)


def test_parse_duration_tolerates_surrounding_space() -> None:
    assert parse_duration("  1d  ") == timedelta(days=1)


@pytest.mark.parametrize(
    "text",
    ["2026-01-01T00:00:00Z", "2026-01-01T00:00:00z", "2026-01-01T00:00:00+00:00", "2026-01-01"],
)
def test_parse_timestamp_accepts_iso_variants(text: str) -> None:
    assert parse_timestamp(text) == datetime(2026, 1, 1, tzinfo=UTC)


def test_parse_timestamp_treats_naive_as_utc() -> None:
    """타임존이 없으면 UTC — 로컬 타임존이 끼어들면 창 경계가 머신마다 달라진다."""
    assert parse_timestamp("2026-01-01T00:00:00") == datetime(2026, 1, 1, tzinfo=UTC)


def test_parse_timestamp_converts_offsets_to_utc() -> None:
    assert parse_timestamp("2026-01-01T09:00:00+09:00") == datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("text", ["yesterday", "", "2026-13-01", "01/01/2026"])
def test_parse_timestamp_rejects_non_iso(text: str) -> None:
    with pytest.raises(FatalError, match="invalid timestamp"):
        parse_timestamp(text)


def test_canonical_timestamp_is_utc_regardless_of_input_offset() -> None:
    """unit_key는 표준형으로 만든다 — 같은 순간이면 표기가 달라도 같은 키여야 한다."""
    kst = parse_timestamp("2026-01-01T09:00:00+09:00")
    utc = parse_timestamp("2026-01-01T00:00:00Z")
    assert canonical_timestamp(kst) == canonical_timestamp(utc) == "2026-01-01T00:00:00Z"


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("iso8601", "2026-01-02T03:04:05Z"),
        ("date", "2026-01-02"),
        ("epoch_s", "1767323045"),
        ("epoch_ms", "1767323045000"),
    ],
)
def test_format_timestamp_covers_every_wire_format(fmt: str, expected: str) -> None:
    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert format_timestamp(moment, fmt) == expected  # pyright: ignore[reportArgumentType]


def test_format_timestamp_normalizes_to_utc_first() -> None:
    moment = datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    assert format_timestamp(moment, "iso8601") == "2026-01-02T03:00:00Z"


def test_iter_windows_yields_grid_aligned_complete_windows() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    windows = list(iter_windows(start, start + timedelta(days=3), timedelta(days=1)))
    assert windows == [
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)),
        (datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 3, tzinfo=UTC)),
        (datetime(2026, 1, 3, tzinfo=UTC), datetime(2026, 1, 4, tzinfo=UTC)),
    ]


def test_iter_windows_drops_the_partial_tail() -> None:
    """끝이 "지금"에 걸린 부분 창을 내보내면 실행 시각마다 경계가 달라진다."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    windows = list(iter_windows(start, start + timedelta(days=2, hours=7), timedelta(days=1)))
    assert [w[1] for w in windows] == [
        datetime(2026, 1, 2, tzinfo=UTC),
        datetime(2026, 1, 3, tzinfo=UTC),
    ]


def test_iter_windows_is_empty_before_the_first_window_completes() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    assert list(iter_windows(start, start + timedelta(hours=23), timedelta(days=1))) == []


def test_iter_windows_is_empty_when_end_precedes_start() -> None:
    start = datetime(2026, 1, 5, tzinfo=UTC)
    assert list(iter_windows(start, start - timedelta(days=1), timedelta(days=1))) == []


def test_iter_windows_rejects_a_non_positive_window() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(FatalError, match="window must be greater than zero"):
        list(iter_windows(start, start + timedelta(days=1), timedelta(0)))
