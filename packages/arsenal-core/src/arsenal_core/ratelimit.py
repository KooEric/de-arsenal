"""토큰 버킷 rate limiter — REST 소스가 rps 상한을 지키도록 acquire()로 스로틀한다.

설계: docs/02-architecture.md M2 rate_limit. penalize()는 429 Retry-After를
흡수해 다음 acquire()가 그만큼 더 기다리게 만든다 (with_retry의 재시도 루프와
맞물려 "실패 → 벌점 → 다음 시도 전 대기"를 구현한다).
"""

import time
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class _MonotonicClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class TokenBucket:
    """rps 상한 토큰 버킷. penalize()로 429 Retry-After를 흡수한다."""

    def __init__(self, rps: float, *, clock: Clock | None = None, burst: int = 1) -> None:
        self._interval = 1.0 / rps
        self._clock = clock or _MonotonicClock()
        self._burst = float(burst)
        self._tokens = float(burst)
        self._last = self._clock.monotonic()
        self._penalty_until = 0.0

    def acquire(self) -> None:
        now = self._clock.monotonic()
        if now < self._penalty_until:
            self._clock.sleep(self._penalty_until - now)
            now = self._clock.monotonic()
        self._tokens = min(self._burst, self._tokens + (now - self._last) / self._interval)
        self._last = now
        if self._tokens < 1.0:
            self._clock.sleep((1.0 - self._tokens) * self._interval)
            self._tokens = self._burst
            self._last = self._clock.monotonic()
        self._tokens -= 1.0

    def penalize(self, seconds: float) -> None:
        self._penalty_until = self._clock.monotonic() + seconds
