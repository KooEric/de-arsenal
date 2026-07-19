import pytest

from arsenal_core.errors import AuthExpiredError, FatalError, RetryableError
from arsenal_core.retry import with_retry


def test_retryable_is_retried_until_success() -> None:
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RetryableError("transient")
        return "ok"

    assert with_retry(flaky, max_attempts=5, base_wait=0) == "ok"
    assert calls["n"] == 3


def test_fatal_is_not_retried() -> None:
    calls = {"n": 0}

    def bad() -> None:
        calls["n"] += 1
        raise FatalError("config")

    with pytest.raises(FatalError):
        with_retry(bad, max_attempts=5, base_wait=0)
    assert calls["n"] == 1


def test_exhausted_attempts_reraises() -> None:
    def always() -> None:
        raise RetryableError("down")

    with pytest.raises(RetryableError):
        with_retry(always, max_attempts=3, base_wait=0)


def test_auth_expired_is_not_blindly_retried() -> None:
    """M2-D: AuthExpiredError는 RetryableError의 서브클래스지만 with_retry는 blind
    backoff 재시도를 하지 않고 즉시 전파해야 한다 (401은 provider.refresh() 없이는
    몇 번을 다시 쳐도 401일 뿐 — runner.py가 refresh 후 재시도를 담당한다)."""
    calls = {"n": 0}

    def always_401() -> None:
        calls["n"] += 1
        raise AuthExpiredError("token expired")

    with pytest.raises(AuthExpiredError):
        with_retry(always_401, max_attempts=5, base_wait=0)
    assert calls["n"] == 1
