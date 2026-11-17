"""Adapter from existing Pugio sinks to deterministic Hasta microbatch writes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa

from arsenal_core.execution import ArtifactRef, OutputProof, SinkCapabilities, canonical_hash
from arsenal_core.spec.models import SinkSpec
from arsenal_core.state import UnitSpec
from hasta.model import CommittedBatch
from pugio.sinks import build_sink
from pugio.sinks.base import Sink


def _record_batch(table: pa.Table) -> pa.RecordBatch:
    batches = table.combine_chunks().to_batches()
    if not batches:
        return pa.RecordBatch.from_arrays([], schema=table.schema)
    return batches[0]


def _schema_hash(table: pa.Table) -> str:
    return canonical_hash(
        [
            {"name": field.name, "type": str(field.type), "nullable": field.nullable}
            for field in table.schema
        ]
    )


class PugioStreamingSink:
    """Give Pugio's established idempotent sinks a stream batch identity."""

    def __init__(self, spec: SinkSpec, *, sink: Sink | None = None) -> None:
        self._spec = spec
        self._sink = sink or build_sink(spec)
        self.capabilities = self._capabilities(spec)

    @staticmethod
    def _capabilities(spec: SinkSpec) -> SinkCapabilities:
        if spec.type == "parquet":
            # Local deterministic files use temp + rename. Raw object stores do
            # not advertise atomic multi-writer publish in stream v1.
            local = "://" not in spec.path
            return SinkCapabilities(idempotent_batch_write=True, atomic_publish=local)
        if spec.type in {"duckdb", "postgres"}:
            return SinkCapabilities(
                idempotent_batch_write=True,
                atomic_publish=True,
                delete_apply=True,
            )
        raise AssertionError(f"unknown sink type: {spec.type}")  # pragma: no cover

    def write_batch(self, batch: CommittedBatch, table: pa.Table) -> OutputProof:
        started = datetime.now(UTC)
        unit = UnitSpec(
            unit_id=batch.batch_id,
            pipeline=batch.pipeline,
            unit_key=f"stream:{batch.stream_name}:{batch.batch_id}",
            payload={"source_id": batch.source_id, "event_hash": batch.event_hash},
        )
        self._sink.write(unit, _record_batch(table))
        return OutputProof(
            run_id=batch.batch_id,
            revision=batch.revision,
            output=self._artifact_ref(),
            input_versions=(batch.source_id,),
            row_count=table.num_rows,
            schema_hash=_schema_hash(table),
            plan_hash=batch.revision,
            started_at=started,
            finished_at=datetime.now(UTC),
        )

    def _artifact_ref(self) -> ArtifactRef:
        if self._spec.type == "parquet":
            return ArtifactRef(uri=self._spec.path, format="parquet")
        if self._spec.type == "duckdb":
            return ArtifactRef(uri=str(Path(self._spec.path)), format="duckdb")
        return ArtifactRef(uri=f"postgres:{self._spec.table}", format="postgres")
