"""Cursor-based REST polling source with stable event identities."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import httpx
import pyarrow as pa

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.execution import canonical_hash, canonical_value
from hasta.model import BatchLimits, MicroBatch, StreamPosition
from hasta.redact import redact_url
from hasta.spec import RestPollSourceSpec


def _dig(value: object, path: str | None) -> object:
    if path is None or path == "":
        return value
    current: object = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        mapping = cast(Mapping[str, object], current)
        current = mapping[part]
    return current


class RestPollingSource:
    """Poll one cursor page at a time without inventing an unstable event key."""

    def __init__(self, spec: RestPollSourceSpec, *, client: httpx.Client | None = None) -> None:
        self._spec = spec
        self._client = client or httpx.Client()
        self._owns_client = client is None
        self.source_id = f"rest_poll:{spec.url}"

    def _get(self, params: dict[str, str | int]) -> httpx.Response:
        """GET one page; failures become classified errors with no credentials in them."""

        where = redact_url(self._spec.url)
        try:
            response = self._client.get(self._spec.url, params=params, headers=self._spec.headers)
        except httpx.TransportError as exc:
            # httpx puts the full request URL (query included) in the message: name the kind only
            raise RetryableError(
                f"rest_poll request to {where} failed: {type(exc).__name__}"
            ) from exc
        status = response.status_code
        if status >= 400:
            message = f"rest_poll request to {where} returned HTTP {status}"
            if status == 429 or status >= 500:
                raise RetryableError(message)
            raise FatalError(message)
        return response

    def poll(self, after: StreamPosition | None, limits: BatchLimits) -> MicroBatch:
        if after is not None and after.value.get("_hasta_exhausted") is True:
            return MicroBatch(after, after, pa.table({}), canonical_hash([]), True)

        params: dict[str, str | int] = {}
        if after is not None and after.value.get("cursor") is not None:
            params[self._spec.cursor.request_param] = str(after.value["cursor"])
        if self._spec.limit_param is not None:
            params[self._spec.limit_param] = limits.max_records
        response = self._get(params)
        payload = cast(object, response.json())
        records = _dig(payload, self._spec.record_path)
        if not isinstance(records, list):
            raise ValueError("rest_poll record_path must resolve to a list of objects")
        typed_records: list[Mapping[str, object]] = []
        for item in cast(list[object], records):
            if not isinstance(item, Mapping):
                raise ValueError("rest_poll record_path must resolve to a list of objects")
            typed_records.append(cast(Mapping[str, object], item))
        if len(typed_records) > limits.max_records:
            raise ValueError("rest_poll response exceeds stream.batch.max_records")
        table = pa.Table.from_pylist([dict(record) for record in typed_records])
        if table.nbytes > limits.max_bytes:
            raise ValueError("rest_poll response exceeds stream.batch.max_bytes")

        event_ids: list[object] = []
        for record in typed_records:
            event_id = _dig(record, self._spec.cursor.event_id_path)
            if event_id is None:
                raise ValueError(
                    "rest_poll requires a non-null stable event id at "
                    f"{self._spec.cursor.event_id_path!r}"
                )
            event_ids.append(canonical_value(event_id))
        raw_cursor = _dig(payload, self._spec.cursor.response_path)
        next_cursor = str(raw_cursor) if raw_cursor not in (None, "") else None
        start = after or StreamPosition({})
        end = StreamPosition({"cursor": next_cursor, "_hasta_exhausted": next_cursor is None})
        return MicroBatch(
            start=start,
            end=end,
            events=table,
            event_hash=canonical_hash(event_ids),
            source_exhausted=next_cursor is None,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
