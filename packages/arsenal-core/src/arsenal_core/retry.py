"""Retryable만 재시도하는 backoff 래퍼. 엔진은 빌린다 — tenacity 사용.

구현: docs/plans/2026-07-08-m1-core-foundation.md Task 5
FatalError는 즉시 전파, RetryableError만 exponential backoff + jitter.

M2-D: AuthExpiredError는 RetryableError의 서브클래스지만 여기서는 재시도 대상에서
제외한다. 401을 blind하게 backoff 재시도해봐야 provider.refresh()가 호출되지
않는 한 계속 401만 받는다 — 갱신 없는 재시도는 의미가 없다. AuthExpiredError는
즉시 전파시켜 runner.py가 "refresh() 후 한 번 더 시도"를 명시적으로 수행하게
한다 (러너 쪽 갱신 루프, docs/02-architecture.md M2-D).
"""

from collections.abc import Callable
from typing import TypeVar

from tenacity import (
    retry,
    retry_if_exception_type,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from arsenal_core.errors import AuthExpiredError, RetryableError

T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BASE_WAIT = 1.0  # seconds
MAX_WAIT = 60.0  # seconds


def with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_wait: float = DEFAULT_BASE_WAIT,
) -> T:
    wrapped = retry(
        retry=retry_if_exception_type(RetryableError)
        & retry_if_not_exception_type(AuthExpiredError),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=base_wait, max=MAX_WAIT),
        reraise=True,
    )(fn)
    return wrapped()
