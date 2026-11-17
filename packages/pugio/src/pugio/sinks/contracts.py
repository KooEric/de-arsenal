"""Capability-aware adapter for Pugio's existing ``Sink.write`` contract.

The concrete sinks keep their v0.2 ``write(unit, batch) -> None`` API. This
module adds the P2 commit boundary around that API: callers that need an
auditable result use :meth:`SinkContractAdapter.write_with_proof`, while older
callers can continue to use :meth:`SinkContractAdapter.write` or the concrete
sink directly.

An :class:`~arsenal_core.execution.OutputProof` is returned only after the
wrapped write succeeds. Atomicity remains the concrete sink's responsibility;
the adapter exposes that fact through an explicit ``SinkCapabilities`` value
instead of making every backend look equally safe.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa

from arsenal_core.execution import ArtifactRef, OutputProof, SinkCapabilities, canonical_hash
from arsenal_core.spec.models import SinkSpec
from arsenal_core.state import UnitSpec
from pugio.sinks.base import Sink

LOCAL_PARQUET_CAPABILITIES = SinkCapabilities(
    idempotent_batch_write=True,
    atomic_publish=True,
)

OBJECT_STORAGE_PARQUET_CAPABILITIES = SinkCapabilities(
    idempotent_batch_write=True,
)

DUCKDB_CAPABILITIES = SinkCapabilities(
    idempotent_batch_write=True,
    atomic_publish=True,
    delete_apply=True,
)

POSTGRES_CAPABILITIES = SinkCapabilities(
    idempotent_batch_write=True,
    atomic_publish=True,
    delete_apply=True,
    concurrent_writers=True,
)

_OBJECT_STORE_SCHEMES = frozenset({"s3", "gcs", "gs"})

Clock = Callable[[], datetime]
OutputRefFactory = Callable[[UnitSpec], ArtifactRef]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class OutputProofContext:
    """Execution identity supplied by the caller at the sink commit boundary."""

    run_id: str
    revision: str
    plan_hash: str
    input_versions: tuple[str, ...] = ()


class SinkContractAdapter:
    """Add capabilities and output proofs without changing a legacy sink."""

    def __init__(
        self,
        sink: Sink,
        *,
        capabilities: SinkCapabilities,
        output_ref: OutputRefFactory,
        clock: Clock = _utcnow,
    ) -> None:
        self._sink = sink
        self.capabilities = capabilities
        self._output_ref = output_ref
        self._clock = clock

    def write(self, unit: UnitSpec, batch: pa.RecordBatch) -> None:
        """Preserve the existing Pugio sink call shape."""
        self._sink.write(unit, batch)

    def write_with_proof(
        self,
        unit: UnitSpec,
        batch: pa.RecordBatch,
        *,
        context: OutputProofContext,
    ) -> OutputProof:
        """Write once and return proof only after the sink reports success."""
        started_at = self._clock()
        self._sink.write(unit, batch)
        finished_at = self._clock()
        output = self._output_ref(unit)
        return OutputProof(
            run_id=context.run_id,
            revision=context.revision,
            output=output,
            input_versions=context.input_versions,
            row_count=batch.num_rows,
            schema_hash=_schema_hash(batch.schema),
            plan_hash=context.plan_hash,
            started_at=started_at,
            finished_at=finished_at,
            output_version=output.version,
        )


def capabilities_for_spec(spec: SinkSpec) -> SinkCapabilities:
    """Return the exact guarantees advertised by a configured sink."""
    if spec.type == "parquet":
        scheme, separator, _ = spec.path.partition("://")
        if separator and scheme.lower() in _OBJECT_STORE_SCHEMES:
            return OBJECT_STORAGE_PARQUET_CAPABILITIES
        return LOCAL_PARQUET_CAPABILITIES
    if spec.type == "duckdb":
        return DUCKDB_CAPABILITIES
    if spec.type == "postgres":
        return POSTGRES_CAPABILITIES
    raise AssertionError(f"unknown sink type: {spec.type}")  # pragma: no cover


def output_ref_for_spec(spec: SinkSpec, unit: UnitSpec) -> ArtifactRef:
    """Describe the deterministic artifact or table targeted by ``unit``."""
    if spec.type == "parquet":
        scheme, separator, _ = spec.path.partition("://")
        if separator and scheme.lower() in _OBJECT_STORE_SCHEMES:
            uri = f"{spec.path.rstrip('/')}/{unit.unit_id}.parquet"
        else:
            uri = str(Path(spec.path) / f"{unit.unit_id}.parquet")
        return ArtifactRef(uri=uri, format="parquet", version=unit.unit_id)
    if spec.type == "duckdb":
        return ArtifactRef(
            uri=f"{Path(spec.path)}#{spec.table}",
            format="duckdb",
            version=unit.unit_id,
        )
    if spec.type == "postgres":
        # Never put the DSN or its environment value in a persisted proof.
        return ArtifactRef(
            uri=f"postgres:///{spec.table}",
            format="postgres",
            version=unit.unit_id,
        )
    raise AssertionError(f"unknown sink type: {spec.type}")  # pragma: no cover


def adapt_sink(
    spec: SinkSpec,
    sink: Sink,
    *,
    clock: Clock = _utcnow,
) -> SinkContractAdapter:
    """Wrap an already-built sink with its configured capability declaration."""
    return SinkContractAdapter(
        sink,
        capabilities=capabilities_for_spec(spec),
        output_ref=lambda unit: output_ref_for_spec(spec, unit),
        clock=clock,
    )


def _schema_hash(schema: pa.Schema) -> str:
    fields = [
        {"name": field.name, "type": str(field.type), "nullable": field.nullable}
        for field in schema
    ]
    return canonical_hash(fields)


__all__ = [
    "DUCKDB_CAPABILITIES",
    "LOCAL_PARQUET_CAPABILITIES",
    "OBJECT_STORAGE_PARQUET_CAPABILITIES",
    "POSTGRES_CAPABILITIES",
    "OutputProofContext",
    "SinkContractAdapter",
    "adapt_sink",
    "capabilities_for_spec",
    "output_ref_for_spec",
]
