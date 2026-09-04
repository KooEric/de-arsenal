"""시간 창(window) 증분 수집의 순수 계산 — 기간 문자열, 타임스탬프, 창 열거.

증분 수집의 unit은 "시간 구간 하나"다. 구간 경계가 결정적이어야 unit ID도
결정적이고(설계 원칙 2), 그래야 재실행이 멱등하다. 그래서 여기 있는 함수는
전부 순수 함수이며, "지금"은 호출자가 주입한다.

창은 `start + k*window` 그리드에 정렬되고 **완결된 창만** 열거한다 — 끝이
"지금"에 걸린 부분 창을 내보내면 같은 창의 경계가 실행 시각마다 달라져
unit ID가 흔들리고 같은 데이터를 다시 받게 된다.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from arsenal_core.errors import FatalError

TimestampFormat = Literal["iso8601", "date", "epoch_s", "epoch_ms"]

_DURATION: Final = re.compile(r"^(\d+)(s|m|h|d|w)$")
_UNIT_SECONDS: Final[dict[str, int]] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}


def parse_duration(text: str) -> timedelta:
    """`"30s"`, `"15m"`, `"6h"`, `"1d"`, `"2w"` → timedelta. 그 외는 FatalError.

    0은 허용한다 (`lag: 0s`가 기본값). 음수·소수·단위 없는 값·`"1 day"` 같은
    자연어는 전부 거부한다 — 스펙 로드 시점에 걸러야 런타임에 조용히 이상한
    창이 생기지 않는다.
    """
    match = _DURATION.match(text.strip())
    if match is None:
        raise FatalError(
            f"invalid duration: {text!r} (expected an integer followed by s|m|h|d|w, e.g. '1d')"
        )
    return timedelta(seconds=int(match.group(1)) * _UNIT_SECONDS[match.group(2)])


def parse_timestamp(text: str) -> datetime:
    """ISO 8601 문자열 → UTC aware datetime. `Z` 접미사와 날짜만 있는 형태도 받는다.

    타임존이 없으면 UTC로 간주한다 — 증분 창의 기준은 항상 UTC다 (API마다 다른
    로컬 타임존 해석이 끼어들면 경계가 흔들린다).
    """
    raw = text.strip()
    normalized = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as e:
        raise FatalError(
            f"invalid timestamp: {text!r} (expected ISO 8601, e.g. '2026-01-01T00:00:00Z')"
        ) from e
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def canonical_timestamp(moment: datetime) -> str:
    """unit_key에 쓰는 표준형 — 전송 포맷(`format`)과 무관하게 항상 이 형태다.

    `format`을 바꿔도 (같은 시간 구간이므로) unit ID가 흔들리지 않게 하려는 것.
    """
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_timestamp(moment: datetime, fmt: TimestampFormat) -> str:
    """API가 요구하는 형태로 직렬화 — 창 경계를 요청 파라미터에 실을 때 쓴다."""
    utc = moment.astimezone(UTC)
    if fmt == "iso8601":
        return canonical_timestamp(utc)
    if fmt == "date":
        return utc.strftime("%Y-%m-%d")
    if fmt == "epoch_s":
        return str(int(utc.timestamp()))
    return str(int(utc.timestamp() * 1000))


def iter_windows(
    start: datetime, end: datetime, window: timedelta
) -> Iterator[tuple[datetime, datetime]]:
    """`start`부터 `window` 간격으로 **완결된** 창 `[창시작, 창끝)`을 연다.

    `end`를 넘어서는 부분 창은 내지 않는다 — 다음 실행에서 완결되면 그때 나온다.
    따라서 데이터는 최대 `window + lag`만큼 늦다(정직한 신선도 상한).
    """
    if window <= timedelta(0):
        raise FatalError("window must be greater than zero")
    cursor = start
    while cursor + window <= end:
        yield cursor, cursor + window
        cursor += window
