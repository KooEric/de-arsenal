"""Send one record to an HTTP endpoint or a Slack webhook."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import quote

import httpx

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.ratelimit import Clock, SystemClock, TokenBucket
from pilum.diff import ActionKind
from pilum.spec import PLACEHOLDER, DestinationSpec, RetrySpec

MASK = "***"
_MAX_WAIT = 60.0
_ERROR_BODY_CHARS = 200


class RecordRejected(FatalError):
    """The destination refused this record (4xx). Retrying the same body cannot help."""


class DeliveryExhausted(RetryableError):
    """Every try of one record failed with a temporary error."""

    def __init__(self, message: str, attempts: int) -> None:
        super().__init__(message)
        self.attempts = attempts


class Destination:
    def __init__(
        self,
        spec: DestinationSpec,
        retry: RetrySpec,
        *,
        client: httpx.Client | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._spec = spec
        self._retry = retry
        self._client = client or httpx.Client(timeout=spec.timeout_seconds)
        self._owns_client = client is None
        self._clock = clock or SystemClock()
        self._bucket = TokenBucket(spec.rps, clock=self._clock) if spec.rps else None
        self._secrets: set[str] = set()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @property
    def supports_idempotency_key(self) -> bool:
        return self._spec.idempotency_header is not None

    # --- templates -------------------------------------------------------

    def _fill(
        self,
        template: str,
        record: Mapping[str, Any] | None,
        *,
        env: bool = True,
        url: bool = False,
    ) -> str:
        """Fill `${ENV_VAR}` (when `env`) and `{column}` (when a record is given).

        Anything not filled stays as written. One regex pass: a filled-in value
        is never scanned again.
        """

        def replace(match: re.Match[str]) -> str:
            env_name, column = match.group(1), match.group(2)
            if env_name:
                if not env:
                    return match.group(0)
                value = os.environ.get(env_name)
                if value is None:
                    raise FatalError(f"environment variable not set: {env_name}")
                self._secrets.add(value)
                return value
            if record is None:
                return match.group(0)
            if column not in record:
                raise FatalError(f"template refers to unknown column '{column}'")
            text = "" if record[column] is None else str(record[column])
            return quote(text, safe="") if url else text

        return PLACEHOLDER.sub(replace, template)

    def _render_body(self, node: Any, record: Mapping[str, Any]) -> Any:
        if isinstance(node, str):
            exact = PLACEHOLDER.fullmatch(node)
            if exact and exact.group(2):
                if exact.group(2) not in record:
                    raise FatalError(f"body refers to unknown column '{exact.group(2)}'")
                return record[exact.group(2)]
            # `${...}` stays literal in a body: secrets belong in headers, not payloads.
            return self._fill(node, record, env=False)
        if isinstance(node, dict):
            mapping = cast(dict[object, object], node)
            return {str(key): self._render_body(value, record) for key, value in mapping.items()}
        if isinstance(node, list):
            return [self._render_body(value, record) for value in cast(list[object], node)]
        return node

    def build_request(
        self, kind: ActionKind, record: Mapping[str, Any], dispatch_id: str
    ) -> httpx.Request:
        spec = self._spec
        headers = {name: self._fill(value, None) for name, value in spec.headers.items()}
        if spec.idempotency_header:
            headers[spec.idempotency_header] = dispatch_id
        if kind == "delete":
            if spec.delete is None:
                raise FatalError("destination has no `delete` request configured")
            url = self._fill(spec.delete.url, record, url=True)
            return self._client.build_request(spec.delete.method, url, headers=headers)
        url = self._fill(spec.url, record, url=True)
        if spec.type == "slack":
            assert spec.text is not None
            body: Any = {"text": self._fill(spec.text, record, env=False)}
        elif spec.body is None:
            body = dict(record)
        else:
            body = self._render_body(spec.body, record)
        return self._client.build_request(spec.method, url, headers=headers, json=body)

    # --- delivery --------------------------------------------------------

    def redact(self, message: str) -> str:
        for secret in self._secrets:
            if len(secret) >= 4:
                message = message.replace(secret, MASK)
        return message

    def send(self, kind: ActionKind, record: Mapping[str, Any], dispatch_id: str) -> int:
        """Deliver one record; returns the number of tries it took.

        Raises RecordRejected (4xx), DeliveryExhausted (temporary failures on
        every try) or FatalError (credentials refused, configuration).
        """

        last_error = ""
        for attempt in range(1, self._retry.max_attempts + 1):
            if self._bucket is not None:
                self._bucket.acquire()
            request = self.build_request(kind, record, dispatch_id)
            try:
                response = self._client.send(request)
            except httpx.TransportError as error:
                last_error = self.redact(f"{type(error).__name__}: {error}")
            else:
                status = response.status_code
                if status < 300:
                    return attempt
                detail = self.redact(f"HTTP {status}: {response.text[:_ERROR_BODY_CHARS]}")
                if status in (401, 403):
                    raise FatalError(
                        f"destination refused the credentials (HTTP {status}); nothing "
                        "was recorded as sent"
                    )
                if status == 404 and kind == "delete":
                    return attempt  # already gone: the delete's goal is met
                if status not in (408, 429) and status < 500:
                    raise RecordRejected(detail)
                last_error = detail
                retry_after = response.headers.get("Retry-After", "")
                if self._bucket is not None and retry_after.isdigit():
                    self._bucket.penalize(min(float(retry_after), _MAX_WAIT))
            if attempt < self._retry.max_attempts:
                self._clock.sleep(
                    min(self._retry.base_wait_seconds * 2 ** (attempt - 1), _MAX_WAIT)
                )
        raise DeliveryExhausted(last_error, self._retry.max_attempts)
