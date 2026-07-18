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


def test_burst_allows_initial_capacity() -> None:
    """rps=1.0, burst=3: 처음 3번의 acquire()는 버스트 용량을 소비만 하고
    sleep 없이 즉시 반환된다 (버스트가 죽어있던 이전 구현은 매번 min(1.0, ...)로
    캡을 걸어 burst>1을 무시했다). 4번째 호출에서 토큰이 바닥나 스로틀이 걸린다:

    interval = 1/1.0 = 1.0
    call1: tokens=3.0 → 소비만, sleep 없음. tokens=2.0
    call2: tokens=2.0 (경과 0) → 소비만, sleep 없음. tokens=1.0
    call3: tokens=1.0 → 소비만, sleep 없음. tokens=0.0
    call4: tokens=0.0 → sleep((1.0-0.0)*1.0)=1.0
    """
    clock = FakeClock()
    tb = TokenBucket(rps=1.0, clock=clock, burst=3)
    for _ in range(3):
        tb.acquire()
    assert clock.slept == []
    tb.acquire()
    assert clock.slept == [pytest.approx(1.0, abs=0.01)]
