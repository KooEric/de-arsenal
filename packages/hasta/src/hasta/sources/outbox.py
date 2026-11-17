"""Ordered Postgres append-only outbox source for Hasta CDC v1."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, cast

import pyarrow as pa

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.execution import canonical_json
from hasta.model import BatchLimits, CdcEvent, MicroBatch, StreamPosition, cdc_event_hash
from hasta.redact import redact
from hasta.spec import DatabaseOutboxSourceSpec


def _identifier_parts(value: str) -> tuple[str, ...]:
    parts = tuple(value.split("."))
    if not parts or any(not part for part in parts):
        raise ValueError(f"invalid SQL identifier {value!r}")
    return parts


def _mapping(value: object, *, name: str, allow_none: bool = False) -> Mapping[str, object] | None:
    """Decode psycopg JSON values without accepting arbitrary scalar payloads."""

    if value is None and allow_none:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise FatalError(f"outbox {name} must be a JSON object") from exc
    if not isinstance(value, Mapping):
        raise FatalError(f"outbox {name} must be a JSON object")
    mapping = cast(Mapping[object, object], value)
    if any(not isinstance(key, str) or not key for key in mapping):
        raise FatalError(f"outbox {name} keys must be non-empty strings")
    return {cast(str, key): mapping[key] for key in mapping}


class PostgresOutboxSource:
    """Poll ``sequence > checkpoint`` in source order and validate every event.

    The source only accepts an append-only event envelope.  It never infers a
    delete from a snapshot table, and an invalid row fails before any sink
    mutation or checkpoint advance can happen.
    """

    def __init__(self, spec: DatabaseOutboxSourceSpec) -> None:
        self._spec = spec
        self._table = _identifier_parts(spec.table)
        self.source_id = f"postgres-outbox:{spec.table}:{spec.sequence_column}"

    def close(self) -> None:
        return None

    def _dsn(self) -> str:
        dsn = os.environ.get(self._spec.dsn_env)
        if not dsn:
            raise FatalError(f"environment variable {self._spec.dsn_env!r} is not set (outbox dsn)")
        return dsn

    @staticmethod
    def _psycopg() -> tuple[Any, Any]:
        """Import the optional Postgres driver only when this source is used."""

        try:
            import psycopg
            from psycopg import sql
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise FatalError("database_outbox requires the hasta postgres extra") from exc
        return psycopg, sql

    def poll(self, after: StreamPosition | None, limits: BatchLimits) -> MicroBatch:
        start = after or StreamPosition({"sequence": -1})
        sequence = start.sequence
        if sequence is None or sequence < -1:
            raise ValueError("outbox checkpoint must contain a sequence >= -1")

        psycopg, sql = self._psycopg()
        names = [
            self._spec.sequence_column,
            self._spec.event_id_column,
            self._spec.operation_column,
            self._spec.payload_column,
            self._spec.occurred_at_column,
        ]
        if self._spec.key_column is not None:
            names.append(self._spec.key_column)
        else:
            names.extend(self._spec.key_columns)
        if self._spec.schema_version_column is not None:
            names.append(self._spec.schema_version_column)
        if len(set(names)) != len(names):
            raise FatalError("outbox envelope columns must not reuse the same name")

        columns = sql.SQL(", ").join(sql.Identifier(name) for name in names)
        query = sql.SQL(
            "SELECT {columns} FROM {table} WHERE {sequence} > %s ORDER BY {sequence} ASC LIMIT %s"
        ).format(
            columns=columns,
            table=sql.Identifier(*self._table),
            sequence=sql.Identifier(self._spec.sequence_column),
        )
        dsn = self._dsn()
        try:
            with (
                psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as con,
                con.cursor() as cur,
            ):
                cur.execute(query, (sequence, limits.max_records))
                rows: list[Mapping[str, object]] = list(cur.fetchall())
        except psycopg.OperationalError as exc:
            raise RetryableError(f"outbox poll failed: {redact(str(exc), [dsn])}") from exc
        except psycopg.Error as exc:
            raise FatalError(f"outbox poll failed: {redact(str(exc), [dsn])}") from exc

        events = tuple(self.event(row) for row in rows)
        previous = sequence
        seen_ids: set[str] = set()
        for event in events:
            if event.sequence <= previous:
                raise FatalError("outbox sequence must be strictly increasing")
            if event.event_id in seen_ids:
                raise FatalError("outbox event_id must not repeat within one ordered page")
            previous = event.sequence
            seen_ids.add(event.event_id)
        if (
            len(canonical_json([event.canonical() for event in events]).encode("utf-8"))
            > limits.max_bytes
        ):
            raise FatalError("outbox response exceeds stream.batch.max_bytes")

        return MicroBatch(
            start=start,
            end=StreamPosition({"sequence": previous}),
            events=pa.table({}),
            event_hash=cdc_event_hash(events),
            source_exhausted=len(events) < limits.max_records,
            changes=events,
        )

    def event(self, row: Mapping[str, object]) -> CdcEvent:
        try:
            raw_event_id = row[self._spec.event_id_column]
            if not isinstance(raw_event_id, str) or not raw_event_id:
                raise ValueError("event_id must be a non-empty string")
            raw_sequence = row[self._spec.sequence_column]
            if isinstance(raw_sequence, bool) or not isinstance(raw_sequence, int):
                raise ValueError("sequence must be an integer")
            raw_op = row[self._spec.operation_column]
            if not isinstance(raw_op, str) or raw_op not in {"I", "U", "D"}:
                raise ValueError("operation must be I, U, or D")
            op = cast(Literal["I", "U", "D"], raw_op)
            occurred = row[self._spec.occurred_at_column]
            if not isinstance(occurred, datetime) or occurred.tzinfo is None:
                raise ValueError("occurred_at must be a timezone-aware timestamp")
            if self._spec.key_column is not None:
                key = _mapping(row[self._spec.key_column], name="key")
            else:
                key = {column: row[column] for column in self._spec.key_columns}
            payload = _mapping(
                row[self._spec.payload_column],
                name="payload",
                allow_none=op == "D",
            )
            schema_version = (
                None
                if self._spec.schema_version_column is None
                or row.get(self._spec.schema_version_column) is None
                else str(row[self._spec.schema_version_column])
            )
            return CdcEvent(
                event_id=raw_event_id,
                sequence=raw_sequence,
                op=op,
                key=key or {},
                payload=payload,
                occurred_at=occurred.astimezone(UTC),
                schema_version=schema_version,
            )
        except KeyError as exc:
            raise FatalError(f"outbox row is missing required column {exc.args[0]!r}") from exc
        except (TypeError, ValueError) as exc:
            raise FatalError(f"invalid immutable outbox event: {exc}") from exc

    def _event(self, row: Mapping[str, object]) -> CdcEvent:
        """Compatibility alias for the pre-release private adapter name."""
        return self.event(row)
