import pytest

from arsenal_core.errors import (
    ArsenalError,
    AuthExpiredError,
    FatalError,
    RetryableError,
    classify_http_status,
)


def test_hierarchy() -> None:
    assert issubclass(AuthExpiredError, RetryableError)
    assert issubclass(RetryableError, ArsenalError)
    assert issubclass(FatalError, ArsenalError)
    assert not issubclass(FatalError, RetryableError)


@pytest.mark.parametrize(
    ("status", "exc_type"),
    [
        (401, AuthExpiredError),
        (429, RetryableError),
        (500, RetryableError),
        (503, RetryableError),
        (400, FatalError),
        (404, FatalError),
    ],
)
def test_classify_http_status(status: int, exc_type: type[ArsenalError]) -> None:
    assert classify_http_status(status) is exc_type


def test_2xx_is_not_an_error() -> None:
    assert classify_http_status(200) is None
