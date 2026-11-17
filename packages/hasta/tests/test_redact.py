"""Credentials never reach Hasta's error messages (0.4 release checklist)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from hasta.model import BatchLimits
from hasta.redact import redact, redact_url
from hasta.sources.rest_poll import RestPollingSource
from hasta.spec import RestPollSourceSpec

from arsenal_core.errors import FatalError, RetryableError

LIMITS = BatchLimits(max_records=10, max_bytes=100_000)


def source(handler: Any, url: str = "https://alice:hunter22@api.test/o?api_key=sk-live-123") -> Any:
    spec = RestPollSourceSpec.model_validate(
        {
            "type": "rest_poll",
            "url": url,
            "headers": {"Authorization": "Bearer tok-secret-9"},
            "cursor": {"request_param": "after", "response_path": "next", "event_id_path": "id"},
        }
    )
    return RestPollingSource(spec, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_redact_url_masks_password_and_query_values() -> None:
    masked = redact_url("https://alice:hunter22@api.test/o?api_key=sk-live-123&page=2")

    assert masked == "https://alice:***@api.test/o?api_key=%2A%2A%2A&page=%2A%2A%2A"
    assert "hunter22" not in masked and "sk-live-123" not in masked


def test_redact_masks_a_dsn_and_its_password_fragment() -> None:
    dsn = "postgresql://app:s3cretpw@db.internal:5432/orders"
    message = 'invalid dsn "postgresql://app:s3cretpw@db..." near s3cretpw'

    assert "s3cretpw" not in redact(f"{message} {dsn}", [dsn])


@pytest.mark.parametrize(("status", "kind"), [(401, FatalError), (503, RetryableError)])
def test_http_failures_are_classified_without_credentials(
    status: int, kind: type[Exception]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    with pytest.raises(kind) as caught:
        source(handler).poll(None, LIMITS)

    text = str(caught.value)
    assert f"HTTP {status}" in text
    for secret in ("hunter22", "sk-live-123", "tok-secret-9"):
        assert secret not in text


def test_transport_failure_is_retryable_and_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    with pytest.raises(RetryableError) as caught:
        source(handler).poll(None, LIMITS)

    assert "sk-live-123" not in str(caught.value) and "hunter22" not in str(caught.value)
