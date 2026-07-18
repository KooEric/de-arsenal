"""M2-C: RestSource의 rate_limit 배선 — TokenBucket.acquire()가 매 요청 전에
호출되고, 429 응답이 penalize()로 이어지는지 검증한다. 실제 sleep 없이
FakeClock을 주입해 결정론적으로 확인한다.
"""

import httpx
import pytest
import respx

from arsenal_core.errors import RetryableError
from arsenal_core.spec.models import PaginationSpec, RateLimitSpec, RestSourceSpec
from pugio.sources.rest import RestSource

OFFSET_PAGINATION = PaginationSpec(mode="offset", param="offset", size_param="limit", size=2)

RATE_LIMITED_SPEC = RestSourceSpec(
    type="rest",
    url="https://api.test/rl-items",
    pagination=OFFSET_PAGINATION,
    rate_limit=RateLimitSpec(rps=2.0),
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@respx.mock
def test_rate_limit_acquires_before_each_request() -> None:
    """rate_limit이 설정되면 매 fetch() 전에 bucket.acquire()가 호출되어 스로틀된다.

    burst=1이므로 1번째 fetch는 무료, 2번째 fetch는 acquire()가 sleep(0.5)를
    유발한다 (TokenBucket 산식: interval=0.5, tokens 0 → sleep((1-0)*0.5)).
    """
    respx.get("https://api.test/rl-items").respond(json=[{"id": 1}, {"id": 2}])
    clock = FakeClock()
    src = RestSource(RATE_LIMITED_SPEC, pipeline="p", client=httpx.Client(), clock=clock)
    units_iter = src.units()
    src.fetch(next(units_iter))
    assert clock.slept == []
    src.fetch(next(units_iter))
    assert clock.slept == [pytest.approx(0.5, abs=0.01)]


@respx.mock
def test_429_penalizes_bucket() -> None:
    """429 + Retry-After: 30 응답은 RetryableError를 던지고, 버킷에 30초 벌점을
    남긴다 — 이후 acquire()는 그 벌점만큼 sleep한다."""
    respx.get("https://api.test/rl-items").respond(status_code=429, headers={"Retry-After": "30"})
    clock = FakeClock()
    src = RestSource(RATE_LIMITED_SPEC, pipeline="p", client=httpx.Client(), clock=clock)
    unit = next(iter(src.units()))
    with pytest.raises(RetryableError):
        src.fetch(unit)
    clock.slept.clear()
    src._bucket.acquire()  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
    assert clock.slept[0] >= 30.0


@respx.mock
def test_no_rate_limit_spec_means_no_throttling() -> None:
    """rate_limit이 None이면 _bucket도 None — 기존 호출부는 전혀 영향받지 않는다."""
    spec = RestSourceSpec(type="rest", url="https://api.test/no-rl", pagination=OFFSET_PAGINATION)
    respx.get("https://api.test/no-rl").respond(json=[{"id": 1}])
    src = RestSource(spec, pipeline="p", client=httpx.Client())
    assert src._bucket is None  # pyright: ignore[reportPrivateUsage]
    result = src.fetch(next(iter(src.units())))
    assert result.batch is not None
