"""Transactional current-state application for immutable CDC envelopes.

The per-event ledger and target mutation share one database transaction.  A
Hasta crash after this transaction but before its SQLite checkpoint commit can
therefore replay an event safely: the ledger identifies the exact immutable
envelope and turns the second apply into a no-op.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import duckdb
import pyarrow as pa

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.execution import ArtifactRef, OutputProof, SinkCapabilities, canonical_hash
from arsenal_core.spec.models import DuckDBSinkSpec, PostgresSinkSpec

Operation = Literal["I", "U", "D"]


class ChangeEventLike(Protocol):
    @property
    def event_id(self) -> str: ...

    @property
    def sequence(self) -> int: ...

    @property
    def op(self) -> Operation: ...

    @property
    def key(self) -> Mapping[str, object]: ...

    @property
    def payload(self) -> Mapping[str, object] | None: ...

    @property
    def occurred_at(self) -> datetime: ...

    @property
    def schema_version(self) -> str | None: ...


def _identifier_parts(value: str) -> tuple[str, ...]:
    parts = tuple(value.split("."))
    if not parts or any(not part for part in parts):
        raise FatalError(f"invalid CDC table identifier {value!r}")
    return parts


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _qualified(parts: Sequence[str]) -> str:
    return ".".join(_quote_ident(part) for part in parts)


def _ledger_parts(table: str) -> tuple[str, ...]:
    parts = _identifier_parts(table)
    return (*parts[:-1], f"{parts[-1]}__arsenal_cdc_events")


def _event_fingerprint(event: ChangeEventLike) -> str:
    """Hash fields that must not change for an immutable ``event_id``."""

    return canonical_hash(
        {
            "event_id": event.event_id,
            "sequence": event.sequence,
            "op": event.op,
            "key": event.key,
            "payload": event.payload,
            "occurred_at": event.occurred_at,
            "schema_version": event.schema_version,
        }
    )


def _proof(
    batch: object, *, uri: str, format: Literal["duckdb", "postgres"], rows: int
) -> OutputProof:
    batch_id = _batch_id(batch)
    now = datetime.now(UTC)
    revision = getattr(batch, "revision", "cdc-v1")
    source_id = getattr(batch, "source_id", None)
    return OutputProof(
        run_id=batch_id,
        revision=revision if isinstance(revision, str) else "cdc-v1",
        output=ArtifactRef(uri=uri, format=format),
        input_versions=(source_id,) if isinstance(source_id, str) else (),
        row_count=rows,
        schema_hash=None,
        plan_hash=canonical_hash({"cdc_batch": batch_id}),
        started_at=now,
        finished_at=now,
    )


def _batch_id(value: object) -> str:
    """Accept Hasta's ``CommittedBatch`` without making Pugio depend on Hasta."""

    batch_id = getattr(value, "batch_id", value)
    if not isinstance(batch_id, str) or not batch_id:
        raise FatalError("CDC batch must provide a non-empty batch_id")
    return batch_id


def _validate_events(events: Sequence[ChangeEventLike], merge_key: Sequence[str]) -> None:
    if not merge_key or any(not key for key in merge_key) or len(set(merge_key)) != len(merge_key):
        raise FatalError("CDC sink merge_key must contain unique non-empty columns")
    key_set = set(merge_key)
    previous_sequence: int | None = None
    envelopes: dict[str, str] = {}
    sequence_events: dict[int, str] = {}
    for event in events:
        if not event.event_id:
            raise FatalError("CDC event_id must be a non-empty string")
        if type(event.sequence) is not int or event.sequence < 0:
            raise FatalError("CDC sequence must be a non-negative integer")
        if event.op not in {"I", "U", "D"}:
            raise FatalError("CDC operation must be I, U, or D")
        if set(event.key) != key_set or any(value is None for value in event.key.values()):
            raise FatalError("CDC event key must exactly match sink merge_key with non-null values")
        if event.op in {"I", "U"}:
            if event.payload is None:
                raise FatalError("CDC insert/update requires a full after-image")
            missing = [key for key in merge_key if key not in event.payload]
            if missing:
                raise FatalError(f"CDC payload missing merge key columns: {', '.join(missing)}")
            if any(event.payload[key] != event.key[key] for key in merge_key):
                raise FatalError("CDC payload merge keys must match the immutable event key")
        elif event.payload is not None:
            raise FatalError("CDC delete must contain key only")
        fingerprint = _event_fingerprint(event)
        existing = envelopes.get(event.event_id)
        if existing is not None and existing != fingerprint:
            raise FatalError("CDC event_id was reused with a different immutable envelope")
        envelopes[event.event_id] = fingerprint
        sequence_event_id = sequence_events.get(event.sequence)
        if sequence_event_id is not None and sequence_event_id != event.event_id:
            raise FatalError("CDC sequence was reused by a different event_id")
        sequence_events[event.sequence] = event.event_id
        if previous_sequence is not None and event.sequence < previous_sequence:
            raise FatalError("CDC events must be ordered by non-decreasing sequence")
        previous_sequence = event.sequence


def _table_exists_duckdb(con: duckdb.DuckDBPyConnection, parts: Sequence[str]) -> bool:
    schema, table = (parts[-2], parts[-1]) if len(parts) > 1 else ("main", parts[0])
    return (
        con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
            [schema, table],
        ).fetchone()
        is not None
    )


@dataclass(frozen=True)
class DuckDBChangeSink:
    """Apply CDC current-state mutations and an immutable event ledger in DuckDB."""

    spec: DuckDBSinkSpec

    capabilities = SinkCapabilities(
        idempotent_batch_write=True,
        atomic_publish=True,
        delete_apply=True,
        concurrent_writers=False,
    )

    @property
    def _table(self) -> tuple[str, ...]:
        return _identifier_parts(self.spec.table)

    @property
    def _ledger(self) -> tuple[str, ...]:
        return _ledger_parts(self.spec.table)

    def apply_changes(self, batch: object, changes: Sequence[ChangeEventLike]) -> OutputProof:
        batch_id = _batch_id(batch)
        _validate_events(changes, self.spec.merge_key)
        con: duckdb.DuckDBPyConnection | None = None
        try:
            con = duckdb.connect(self.spec.path)
            con.execute("BEGIN")
            con.execute(
                f"CREATE TABLE IF NOT EXISTS {_qualified(self._ledger)} "
                "(event_id VARCHAR PRIMARY KEY, sequence BIGINT NOT NULL, "
                "event_hash VARCHAR NOT NULL, batch_id VARCHAR NOT NULL)"
            )
            applied = 0
            for event in changes:
                fingerprint = _event_fingerprint(event)
                existing = con.execute(
                    "SELECT sequence, event_hash FROM "
                    f"{_qualified(self._ledger)} WHERE event_id = ?",
                    [event.event_id],
                ).fetchone()
                if existing is not None:
                    if existing != (event.sequence, fingerprint):
                        raise FatalError("CDC event_id conflicts with an already-applied envelope")
                    continue
                con.execute(
                    f"INSERT INTO {_qualified(self._ledger)} "
                    "(event_id, sequence, event_hash, batch_id) VALUES (?, ?, ?, ?)",
                    [event.event_id, event.sequence, fingerprint, batch_id],
                )
                self._apply_one(con, event)
                applied += 1
            con.execute("COMMIT")
            return _proof(batch, uri=self.spec.path, format="duckdb", rows=applied)
        except (duckdb.IOException, duckdb.TransactionException) as exc:
            if con is not None:
                with contextlib.suppress(Exception):
                    con.execute("ROLLBACK")
            raise RetryableError(f"duckdb CDC apply failed for batch {batch_id}: {exc}") from exc
        except FatalError:
            if con is not None:
                with contextlib.suppress(Exception):
                    con.execute("ROLLBACK")
            raise
        except Exception as exc:
            if con is not None:
                with contextlib.suppress(Exception):
                    con.execute("ROLLBACK")
            raise FatalError(f"duckdb CDC apply failed for batch {batch_id}: {exc}") from exc
        finally:
            if con is not None:
                con.close()

    def _apply_one(self, con: duckdb.DuckDBPyConnection, event: ChangeEventLike) -> None:
        table = _qualified(self._table)
        where = " AND ".join(f"{_quote_ident(key)} = ?" for key in self.spec.merge_key)
        key_values = [event.key[key] for key in self.spec.merge_key]
        if event.op == "D":
            # A delete before any insert is a safe no-op; without a payload it
            # cannot manufacture an arbitrary target schema.
            if _table_exists_duckdb(con, self._table):
                con.execute(f"DELETE FROM {table} WHERE {where}", key_values)
            return
        assert event.payload is not None
        payload = pa.Table.from_pylist([dict(event.payload)])
        columns = list(payload.schema.names)
        if not columns:
            raise FatalError("CDC insert/update payload must not be empty")
        con.register("_arsenal_cdc_payload", payload)
        cols = ", ".join(_quote_ident(column) for column in columns)
        try:
            con.execute(
                f"CREATE TABLE IF NOT EXISTS {table} AS SELECT {cols} "
                "FROM _arsenal_cdc_payload LIMIT 0"
            )
            con.execute(f"DELETE FROM {table} WHERE {where}", key_values)
            con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _arsenal_cdc_payload")
        finally:
            con.unregister("_arsenal_cdc_payload")


@dataclass(frozen=True)
class PostgresChangeSink:
    """Apply CDC current-state mutations and its event ledger in Postgres."""

    spec: PostgresSinkSpec

    capabilities = SinkCapabilities(
        idempotent_batch_write=True,
        atomic_publish=True,
        delete_apply=True,
        concurrent_writers=True,
    )

    @property
    def _table(self) -> tuple[str, ...]:
        return _identifier_parts(self.spec.table)

    @property
    def _ledger(self) -> tuple[str, ...]:
        return _ledger_parts(self.spec.table)

    def apply_changes(self, batch: object, changes: Sequence[ChangeEventLike]) -> OutputProof:
        batch_id = _batch_id(batch)
        _validate_events(changes, self.spec.merge_key)
        dsn = os.environ.get(self.spec.dsn_env)
        if not dsn:
            raise FatalError(
                f"environment variable {self.spec.dsn_env!r} is not set (postgres dsn)"
            )
        try:
            import psycopg
            from psycopg import sql

            from pugio.sinks.postgres import (
                _classify_operational_error as classify_operational_error,  # pyright: ignore[reportPrivateUsage]
            )
            from pugio.sinks.postgres import pg_column_type as postgres_type_for_arrow_field
        except ImportError as exc:  # pragma: no cover - install-extra guard
            raise FatalError("postgres CDC sink requires pugio[postgres]") from exc

        try:
            with psycopg.connect(dsn) as con, con.transaction():
                con.execute(
                    sql.SQL(
                        "CREATE TABLE IF NOT EXISTS {} "
                        "(event_id text PRIMARY KEY, sequence bigint NOT NULL, "
                        "event_hash text NOT NULL, batch_id text NOT NULL)"
                    ).format(sql.Identifier(*self._ledger))
                )
                applied = 0
                for event in changes:
                    fingerprint = _event_fingerprint(event)
                    inserted = con.execute(
                        sql.SQL(
                            "INSERT INTO {} (event_id, sequence, event_hash, batch_id) "
                            "VALUES (%s, %s, %s, %s) "
                            "ON CONFLICT (event_id) DO NOTHING RETURNING event_id"
                        ).format(sql.Identifier(*self._ledger)),
                        (event.event_id, event.sequence, fingerprint, batch_id),
                    ).fetchone()
                    if inserted is None:
                        existing = con.execute(
                            sql.SQL(
                                "SELECT sequence, event_hash FROM {} WHERE event_id = %s"
                            ).format(sql.Identifier(*self._ledger)),
                            (event.event_id,),
                        ).fetchone()
                        if existing != (event.sequence, fingerprint):
                            raise FatalError(
                                "CDC event_id conflicts with an already-applied envelope"
                            )
                        continue
                    self._apply_one(con, event, sql, postgres_type_for_arrow_field)
                    applied += 1
            return _proof(
                batch, uri=f"postgres:///{self.spec.table}", format="postgres", rows=applied
            )
        except psycopg.OperationalError as exc:
            error_cls = classify_operational_error(exc)
            raise error_cls(f"postgres CDC apply failed for batch {batch_id}: {exc}") from exc
        except FatalError:
            raise
        except psycopg.Error as exc:
            raise FatalError(f"postgres CDC apply failed for batch {batch_id}: {exc}") from exc

    def _table_exists(self, con: Any, sql: Any) -> bool:
        regclass = ".".join(_quote_ident(part) for part in self._table)
        return con.execute(sql.SQL("SELECT to_regclass(%s)"), (regclass,)).fetchone()[0] is not None

    def _apply_one(self, con: Any, event: ChangeEventLike, sql: Any, pg_type: Any) -> None:
        table = sql.Identifier(*self._table)
        where = sql.SQL(" AND ").join(
            sql.SQL("{} = %s").format(sql.Identifier(key)) for key in self.spec.merge_key
        )
        key_values = tuple(event.key[key] for key in self.spec.merge_key)
        if event.op == "D":
            if self._table_exists(con, sql):
                con.execute(sql.SQL("DELETE FROM {} WHERE {}").format(table, where), key_values)
            return
        assert event.payload is not None
        payload = pa.Table.from_pylist([dict(event.payload)])
        if not payload.schema.names:
            raise FatalError("CDC insert/update payload must not be empty")
        columns = [
            sql.SQL("{} {}").format(sql.Identifier(field.name), pg_type(field))
            for field in payload.schema
        ]
        con.execute(
            sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
                table,
                sql.SQL(", ").join(columns),
            )
        )
        con.execute(sql.SQL("DELETE FROM {} WHERE {}").format(table, where), key_values)
        names = payload.schema.names
        con.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                table,
                sql.SQL(", ").join(sql.Identifier(name) for name in names),
                sql.SQL(", ").join(sql.Placeholder() for _ in names),
            ),
            tuple(event.payload[name] for name in names),
        )


__all__ = ["DuckDBChangeSink", "PostgresChangeSink"]
