"""TokenBucket 단위 테스트 — 실제 sleep 없이 FakeClock으로 결정론적 검증.

M2-C: rps 상한 토큰 버킷 + penalize()로 429 Retry-After 흡수.
"""

import pytest

from arsenal_core.ratelimit import TokenBucket


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_token_bucket_throttles_to_rps() -> None:
    """rps=2.0, burst=1: 1번째 acquire는 무료(버스트), 이후 3번은 각각 0.5s씩
    쉰다 — 합계 1.5s (직접 계산한 값, 추측이 아니다):

    interval = 1/2.0 = 0.5
    call1: tokens=1.0 → 소비만, sleep 없음.
    call2: tokens=0.0 → sleep((1-0)*0.5)=0.5
    call3: tokens=0.0 (경과시간 0이므로 리필 없음) → sleep(0.5)
    call4: 동일 → sleep(0.5)
    합계 = 0 + 0.5 + 0.5 + 0.5 = 1.5
    """
    clock = FakeClock()
    tb = TokenBucket(rps=2.0, clock=clock)
    for _ in range(4):
        tb.acquire()
    assert sum(clock.slept) == pytest.approx(1.5, abs=0.01)


def test_penalize_respects_retry_after() -> None:
    clock = FakeClock()
    tb = TokenBucket(rps=2.0, clock=clock)
    tb.penalize(30.0)
    tb.acquire()
    assert clock.slept[0] >= 30.0
