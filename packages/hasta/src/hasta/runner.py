"""Leased microbatch processing: poll, validate, write, then fenced commit."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

import pyarrow as pa
from scutum import ContractColumn, DataContract

from arsenal_core.errors import FatalError
from arsenal_core.execution import (
    ArtifactRef,
    LeaseToken,
    OutputProof,
    SinkCapabilities,
    canonical_hash,
    canonical_json,
    spec_revision,
)
from arsenal_core.schema import compare_schema_snapshots
from arsenal_core.state import LeaseLostError, StateStore, UnitSpec
from hasta.model import (
    BatchLimits,
    CdcEvent,
    CommittedBatch,
    MicroBatch,
    StreamingSink,
    StreamingSource,
    StreamPosition,
    batch_identity,
    cdc_event_hash,
)
from hasta.sink import PugioStreamingSink
from hasta.sources import PostgresOutboxSource, RestPollingSource
from hasta.spec import EventTimeSpec, StreamSpec
from pugio.dlq import write_dlq
from pugio.sinks.cdc import DuckDBChangeSink, PostgresChangeSink
from pugio.validate.gate import Violation, check

logger = logging.getLogger("hasta.runner")


class LeaseUnavailableError(RuntimeError):
    """Another live runner owns this stream's lease."""


class StreamValidationError(FatalError):
    """A batch failed contract, schema, or validation before it reached the sink."""


@runtime_checkable
class CdcApplySink(Protocol):
    """Transactional sink contract for immutable CDC envelopes."""

    capabilities: SinkCapabilities

    def apply_changes(
        self,
        batch: CommittedBatch,
        changes: Sequence[CdcEvent],
    ) -> OutputProof: ...


@dataclass(frozen=True)
class StreamReport:
    polls: int = 0
    committed: int = 0
    skipped: int = 0
    quarantined: int = 0
    exhausted: bool = False


def _schema_json(table: pa.Table) -> str:
    return json.dumps(
        [
            {"name": field.name, "type": str(field.type), "nullable": field.nullable}
            for field in table.schema
        ],
        separators=(",", ":"),
    )


def _record_batch(table: pa.Table) -> pa.RecordBatch:
    batches = table.combine_chunks().to_batches()
    if not batches:
        return pa.RecordBatch.from_arrays([], schema=table.schema)
    return batches[0]


def _build_contract(spec: StreamSpec) -> DataContract | None:
    if spec.contract is None:
        return None
    return DataContract(
        name=spec.name,
        columns=[
            ContractColumn(name=column.name, type=column.type, nullable=column.nullable)
            for column in spec.contract.columns
        ],
        allow_extra=spec.contract.allow_extra,
    )


def _parse_duration(value: str) -> timedelta:
    match = re.fullmatch(
        r"P(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?",
        value,
    )
    if match is None:
        raise ValueError("allowed_lateness must be an ISO-8601 duration such as PT10M")
    hours, minutes, seconds = (float(part) if part is not None else 0.0 for part in match.groups())
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def _as_utc(value: object, field: str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise StreamValidationError(
            f"event-time field {field!r} must contain timezone-aware values"
        )
    return value.astimezone(UTC)


def validate_cdc_batch(microbatch: MicroBatch, changes: tuple[CdcEvent, ...]) -> None:
    """Reject malformed source ordering before an apply sink observes it."""

    start = microbatch.start.sequence
    end = microbatch.end.sequence
    if start is None or start < -1 or end is None:
        raise StreamValidationError("outbox CDC positions must contain an integer sequence")
    previous = start
    seen_ids: set[str] = set()
    for event in changes:
        if event.sequence <= previous:
            raise StreamValidationError("outbox CDC events must be strictly ordered by sequence")
        if event.event_id in seen_ids:
            raise StreamValidationError("outbox CDC event_id repeated within one microbatch")
        previous = event.sequence
        seen_ids.add(event.event_id)
    if end != previous:
        raise StreamValidationError("outbox CDC end position must equal the final event sequence")
    if microbatch.event_hash != cdc_event_hash(changes):
        raise StreamValidationError("outbox CDC event hash does not match its immutable envelopes")


class StreamRunner:
    """Process REST microbatches under an SQLite lease and fence."""

    def __init__(
        self,
        spec: StreamSpec,
        *,
        source: StreamingSource | None = None,
        sink: StreamingSink | CdcApplySink | None = None,
        store: StateStore | None = None,
        owner_id: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.spec = spec
        self._source = source or self._build_source(spec)
        self._sink = sink or self._build_sink(spec)
        self._store = store or StateStore(Path(spec.state_dir) / f"{spec.name}.db")
        self._owns_store = store is None
        self._owner_id = owner_id or f"hasta-{uuid.uuid4()}"
        self._sleep = sleep
        self._revision = spec_revision(spec.revision_payload())
        self._contract = _build_contract(spec)
        self._validate_sink_capabilities()

    @staticmethod
    def _build_source(spec: StreamSpec) -> StreamingSource:
        if spec.source.type == "rest_poll":
            return RestPollingSource(spec.source)
        if spec.source.type == "database_outbox":
            return PostgresOutboxSource(spec.source)
        if spec.source.type == "database_table_cursor":
            raise NotImplementedError(
                "database_table_cursor is not CDC: it cannot observe hard deletes; "
                "use database_outbox"
            )
        raise NotImplementedError(f"unsupported stream source: {spec.source.type}")

    @staticmethod
    def _build_sink(spec: StreamSpec) -> StreamingSink | CdcApplySink:
        if spec.source.type != "database_outbox":
            return PugioStreamingSink(spec.sink)
        if spec.sink.type == "duckdb":
            return DuckDBChangeSink(spec.sink)  # type: ignore[return-value]
        if spec.sink.type == "postgres":
            return PostgresChangeSink(spec.sink)  # type: ignore[return-value]
        raise AssertionError("database_outbox sink validation should reject parquet")

    def _validate_sink_capabilities(self) -> None:
        capabilities = getattr(self._sink, "capabilities", None)
        if capabilities is None:
            return  # Custom/test sinks carry their own idempotency contract.
        if not isinstance(capabilities, SinkCapabilities):
            raise TypeError("streaming sink capabilities must use SinkCapabilities")
        if not (capabilities.idempotent_batch_write and capabilities.atomic_publish):
            raise ValueError("streaming requires idempotent_batch_write and atomic_publish")
        if self.spec.source.type == "database_outbox" and not capabilities.delete_apply:
            raise ValueError("database_outbox requires a sink with transactional delete_apply")

    @property
    def resource(self) -> str:
        return f"stream:{self.spec.name}:{self.spec.stream.name}"

    def run_once(self) -> StreamReport:
        return self.run(max_batches=1)

    def run(
        self,
        *,
        max_batches: int | None = None,
        wait_between_polls: bool = False,
    ) -> StreamReport:
        if max_batches is not None and max_batches < 1:
            raise ValueError("max_batches must be at least 1")
        lease = self._store.acquire_lease(
            self.resource,
            self._owner_id,
            self.spec.stream.checkpoint.lease_ttl_seconds,
        )
        if lease is None:
            raise LeaseUnavailableError(f"stream lease is held: {self.resource}")
        polls = committed = skipped = quarantined = 0
        exhausted = False
        try:
            while max_batches is None or polls < max_batches:
                checkpoint = self._store.checkpoint(self.spec.name, self.spec.stream.name)
                after = StreamPosition(checkpoint.position) if checkpoint is not None else None
                if after is not None and after.value.get("_hasta_exhausted") is True:
                    exhausted = True
                    break
                refreshed = self._store.renew_lease(
                    lease,
                    self.spec.stream.checkpoint.lease_ttl_seconds,
                )
                if refreshed is None:
                    raise LeaseLostError(f"lease lost before polling {self.resource!r}")
                lease = refreshed
                microbatch = self._source.poll(after, self.spec.stream.batch.limits())
                polls += 1
                result = self._process_batch(
                    microbatch,
                    lease,
                    checkpoint.watermark if checkpoint is not None else None,
                )
                committed += result.committed
                skipped += result.skipped
                quarantined += result.quarantined
                if microbatch.source_exhausted:
                    exhausted = True
                    break
                if wait_between_polls and (max_batches is None or polls < max_batches):
                    self._sleep(self.spec.stream.poll_interval_seconds)
            return StreamReport(polls, committed, skipped, quarantined, exhausted)
        finally:
            self._store.release_lease(lease)

    def _process_batch(
        self,
        microbatch: MicroBatch,
        lease: LeaseToken,
        existing_watermark: str | None,
    ) -> StreamReport:
        batch_id = batch_identity(
            pipeline=self.spec.name,
            stream_name=self.spec.stream.name,
            revision=self._revision,
            source_id=self._source.source_id,
            start=microbatch.start,
            end=microbatch.end,
            event_hash=microbatch.event_hash,
        )
        batch = CommittedBatch(
            batch_id=batch_id,
            pipeline=self.spec.name,
            stream_name=self.spec.stream.name,
            revision=self._revision,
            source_id=self._source.source_id,
            start=microbatch.start,
            end=microbatch.end,
            event_hash=microbatch.event_hash,
        )
        ledger = self._store.plan_stream_batch(
            batch_id=batch_id,
            pipeline=batch.pipeline,
            stream_name=batch.stream_name,
            revision=batch.revision,
            start_position=batch.start.canonical(),
            end_position=batch.end.canonical(),
            event_hash=batch.event_hash,
        )
        if ledger.status == "committed":
            return StreamReport(skipped=1, exhausted=microbatch.source_exhausted)

        changes = microbatch.changes
        if changes is not None:
            return self._process_cdc_batch(microbatch, batch, lease, changes)
        if not isinstance(self._sink, StreamingSink):
            raise ValueError("Arrow microbatches require a streaming write sink")
        table = microbatch.events
        table, late_rows, watermark = self._validate_and_filter(
            table,
            batch,
            existing_watermark,
        )
        # A terminal empty poll advances the checkpoint but writes no artifact.
        proof = (
            self._empty_proof(batch, table)
            if table.num_rows == 0
            else self._sink.write_batch(batch, table)
        )
        self._store.commit_stream_batch(
            batch_id,
            lease=lease,
            checkpoint_position=batch.end.canonical(),
            generation=self._checkpoint_generation(),
            watermark=watermark,
            output_proof=proof,
            rows=table.num_rows,
            bytes=table.nbytes,
            late_rows=late_rows,
        )
        return StreamReport(committed=1, exhausted=microbatch.source_exhausted)

    def _process_cdc_batch(
        self,
        microbatch: MicroBatch,
        batch: CommittedBatch,
        lease: LeaseToken,
        changes: tuple[CdcEvent, ...],
    ) -> StreamReport:
        """Apply ordered immutable outbox envelopes through a transactional sink."""

        validate_cdc_batch(microbatch, changes)
        if not isinstance(self._sink, CdcApplySink):
            raise ValueError("database_outbox requires a CDC apply sink")
        proof = self._sink.apply_changes(batch, changes)
        self._store.commit_stream_batch(
            batch.batch_id,
            lease=lease,
            checkpoint_position=batch.end.canonical(),
            generation=self._checkpoint_generation(),
            output_proof=proof,
            rows=len(changes),
            bytes=len(canonical_json([event.canonical() for event in changes]).encode("utf-8")),
        )
        return StreamReport(committed=1, exhausted=microbatch.source_exhausted)

    def _checkpoint_generation(self) -> int:
        checkpoint = self._store.checkpoint(self.spec.name, self.spec.stream.name)
        return checkpoint.generation if checkpoint is not None else 0

    def _validate_and_filter(
        self,
        table: pa.Table,
        batch: CommittedBatch,
        existing_watermark: str | None,
    ) -> tuple[pa.Table, int, str | None]:
        if table.num_rows == 0:
            return table, 0, existing_watermark
        self._check_schema(table)
        self._check_contract_and_rules(table, batch)
        if self.spec.stream.event_time is None:
            return table, 0, existing_watermark
        return self._apply_watermark(table, batch, self.spec.stream.event_time, existing_watermark)

    def _check_schema(self, table: pa.Table) -> None:
        current = _schema_json(table)
        previous = self._store.last_schema(self.spec.name)
        if previous is not None:
            drift = compare_schema_snapshots(previous, current)
            if drift.has_changes:
                message = f"schema drift for stream {self.spec.name!r}: {drift.summary()}"
                if self.spec.schema_drift == "block":
                    raise StreamValidationError(message)
                if self.spec.schema_drift == "warn":
                    logger.warning(message)
        self._store.snapshot_schema(self.spec.name, current)

    def _check_contract_and_rules(self, table: pa.Table, batch: CommittedBatch) -> None:
        record_batch = _record_batch(table)
        if self._contract is not None:
            report = self._contract.check(record_batch)
            if not report.ok:
                violations = [
                    Violation("contract", violation.field, 1) for violation in report.violations
                ]
                policy = (
                    self.spec.contract.on_violation if self.spec.contract is not None else "block"
                )
                self._handle_violations(violations, table, batch, policy)
        if self.spec.validation is not None:
            report = check(record_batch, self.spec.validation.rules)
            if not report.ok:
                self._handle_violations(
                    report.violations,
                    table,
                    batch,
                    self.spec.validation.on_violation,
                )

    def _handle_violations(
        self,
        violations: list[Violation],
        table: pa.Table,
        batch: CommittedBatch,
        policy: str,
    ) -> None:
        if policy == "warn":
            logger.warning("stream batch %s validation violations: %s", batch.batch_id, violations)
            return
        if policy == "quarantine":
            write_dlq(
                self.spec.state_dir,
                self.spec.name,
                self._dlq_unit(batch),
                _record_batch(table),
                violations,
            )
        raise StreamValidationError(
            f"stream batch {batch.batch_id} failed validation: "
            + "; ".join(f"{item.rule}:{item.field}={item.count}" for item in violations)
        )

    def _apply_watermark(
        self,
        table: pa.Table,
        batch: CommittedBatch,
        config: EventTimeSpec,
        existing_watermark: str | None,
    ) -> tuple[pa.Table, int, str | None]:
        try:
            values = table.column(config.field).to_pylist()
        except KeyError as error:
            raise StreamValidationError(f"event-time field {config.field!r} is missing") from error
        event_times = [_as_utc(value, config.field) for value in values]
        current = datetime.fromisoformat(existing_watermark) if existing_watermark else None
        if current is not None:
            current = current.astimezone(UTC)
        late_mask = [current is not None and event_time < current for event_time in event_times]
        late_rows = sum(late_mask)
        if late_rows and config.on_late == "quarantine":
            late_table = table.filter(pa.array(late_mask))
            write_dlq(
                self.spec.state_dir,
                self.spec.name,
                self._dlq_unit(batch),
                _record_batch(late_table),
                [Violation("late", config.field, late_rows)],
            )
        output = table
        if late_rows and config.on_late in {"drop", "quarantine"}:
            output = table.filter(pa.array([not value for value in late_mask]))
        candidate = max(event_times) - _parse_duration(config.allowed_lateness)
        watermark = max(current, candidate) if current is not None else candidate
        return output, late_rows, watermark.isoformat()

    def _dlq_unit(self, batch: CommittedBatch) -> UnitSpec:
        return UnitSpec(
            unit_id=batch.batch_id,
            pipeline=batch.pipeline,
            unit_key=f"stream:{batch.stream_name}:{batch.batch_id}",
            payload={"source_id": batch.source_id, "event_hash": batch.event_hash},
        )

    @staticmethod
    def _empty_proof(batch: CommittedBatch, table: pa.Table) -> OutputProof:
        now = datetime.now(UTC)
        return OutputProof(
            run_id=batch.batch_id,
            revision=batch.revision,
            output=ArtifactRef(uri="hasta://no-output", format="parquet"),
            input_versions=(batch.source_id,),
            row_count=0,
            schema_hash=canonical_hash(str(table.schema)),
            plan_hash=batch.revision,
            started_at=now,
            finished_at=now,
        )

    def close(self) -> None:
        self._source.close()
        if self._owns_store:
            self._store.close()


class CdcRunner:
    """Small programmatic CDC runner used by integration tests and embedders.

    ``StreamRunner`` is the YAML-facing runner.  This adapter exposes the same
    lease, deterministic batch identity, apply, and checkpoint sequence for
    callers that already own validated source and sink objects.
    """

    def __init__(
        self,
        *,
        state: StateStore,
        pipeline: str,
        stream_name: str,
        revision: str,
        source: StreamingSource,
        sink: CdcApplySink,
        owner_id: str | None = None,
        lease_ttl_seconds: float = 60,
        fault_hook: Callable[[str], None] | None = None,
    ) -> None:
        if not pipeline or not stream_name or not revision:
            raise ValueError("CDC pipeline, stream_name, and revision must be non-empty")
        if lease_ttl_seconds <= 0:
            raise ValueError("CDC lease_ttl_seconds must be positive")
        if not (
            sink.capabilities.idempotent_batch_write
            and sink.capabilities.atomic_publish
            and sink.capabilities.delete_apply
        ):
            raise ValueError(
                "CDC sink requires idempotent_batch_write, atomic_publish, and delete_apply"
            )
        self._state = state
        self._pipeline = pipeline
        self._stream_name = stream_name
        self._revision = revision
        self._source = source
        self._sink = sink
        self._owner_id = owner_id or f"hasta-cdc-{uuid.uuid4()}"
        self._lease_ttl_seconds = lease_ttl_seconds
        self._fault_hook = fault_hook

    @property
    def resource(self) -> str:
        return f"stream:{self._pipeline}:{self._stream_name}"

    def run_once(self, limits: BatchLimits | None = None) -> bool:
        """Apply one available CDC page and return whether it contained events."""

        limits = limits or BatchLimits(10_000, 64 * 1024 * 1024, 20)
        lease = self._state.acquire_lease(self.resource, self._owner_id, self._lease_ttl_seconds)
        if lease is None:
            raise LeaseUnavailableError(f"stream lease is held: {self.resource}")
        try:
            checkpoint = self._state.checkpoint(self._pipeline, self._stream_name)
            after = StreamPosition(checkpoint.position) if checkpoint is not None else None
            microbatch = self._source.poll(after, limits)
            changes = microbatch.changes
            if changes is None:
                raise TypeError("CdcRunner source must return immutable CdcEvent envelopes")
            validate_cdc_batch(microbatch, changes)
            batch_id = batch_identity(
                pipeline=self._pipeline,
                stream_name=self._stream_name,
                revision=self._revision,
                source_id=self._source.source_id,
                start=microbatch.start,
                end=microbatch.end,
                event_hash=microbatch.event_hash,
            )
            batch = CommittedBatch(
                batch_id=batch_id,
                pipeline=self._pipeline,
                stream_name=self._stream_name,
                revision=self._revision,
                source_id=self._source.source_id,
                start=microbatch.start,
                end=microbatch.end,
                event_hash=microbatch.event_hash,
            )
            ledger = self._state.plan_stream_batch(
                batch_id=batch_id,
                pipeline=batch.pipeline,
                stream_name=batch.stream_name,
                revision=batch.revision,
                start_position=batch.start.canonical(),
                end_position=batch.end.canonical(),
                event_hash=batch.event_hash,
            )
            if ledger.status == "committed":
                return False
            proof = self._sink.apply_changes(batch, changes)
            if self._fault_hook is not None:
                self._fault_hook("after_sink_write")
            self._state.commit_stream_batch(
                batch.batch_id,
                lease=lease,
                checkpoint_position=batch.end.canonical(),
                generation=checkpoint.generation if checkpoint is not None else 0,
                output_proof=proof,
                rows=len(changes),
                bytes=len(canonical_json([event.canonical() for event in changes]).encode("utf-8")),
            )
            return bool(changes)
        finally:
            self._state.release_lease(lease)

    def close(self) -> None:
        self._source.close()


HastaRunner = CdcRunner
"""Compatibility name for the programmatic CDC runner."""
