"""Retryable만 재시도하는 backoff 래퍼. 엔진은 빌린다 — tenacity 사용.

구현: docs/plans/2026-07-08-m1-core-foundation.md Task 5
FatalError는 즉시 전파, RetryableError만 exponential backoff + jitter.
"""

from collections.abc import Callable
from typing import TypeVar

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from arsenal_core.errors import RetryableError

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
        retry=retry_if_exception_type(RetryableError),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=base_wait, max=MAX_WAIT),
        reraise=True,
    )(fn)
    return wrapped()
