"""Iceberg committer and versioned readers that run inside the Spark driver.

The committer is the only component allowed to make a Ballista result
visible. It publishes one Iceberg snapshot per logical run and tags it with the
run identity, so a retried or duplicated attempt finds the snapshot it already
committed instead of committing again:

1. Look for a snapshot whose summary carries this run id. Its logical hash
   (revision, plan, input versions, output) must match; then return its proof
   without writing. This covers a retried *attempt* of the same run, whose
   manifest differs only in attempt number and lease.
2. Refuse when the table's current snapshot was published by a *newer*
   manifest: a stale attempt must never overwrite a later run's result.
3. Replace the table contents in one Iceberg commit (``overwrite(true)``),
   tagging the snapshot via ``snapshot-property.*`` write options.

Two attempts racing past step 1 both replace the table with the same
deterministic output (versioned inputs + identical plan), so the visible
result is the same single dataset. The window between step 2 and the commit
is not closed by Iceberg's optimistic concurrency for full overwrites; that
limit is documented in ``docs/08-limits.md``.

Nothing here imports pyspark at module import time; the worker image provides
it and tests drive these classes with fakes.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from arsenal_core.errors import FatalError
from arsenal_core.execution import ArtifactRef, OutputProof, SinkCapabilities, canonical_hash
from ballista.artifacts import RunManifest
from ballista.plan import plan_hash

RUN_ID_PROPERTY = "de-arsenal.run-id"
MANIFEST_HASH_PROPERTY = "de-arsenal.manifest-hash"
MANIFEST_CREATED_PROPERTY = "de-arsenal.manifest-created-at"
LOGICAL_HASH_PROPERTY = "de-arsenal.logical-hash"
DEFAULT_SPARK_CATALOG = "arsenal"

_IDENTIFIER_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

ICEBERG_COMMIT_CAPABILITIES = SinkCapabilities(
    idempotent_batch_write=True,
    atomic_publish=True,
    delete_apply=False,
    concurrent_writers=True,
    input_versioning=True,
)


class StaleRunError(FatalError):
    """A newer run already published to the output table."""


def logical_hash(manifest: RunManifest) -> str:
    """Identity of the *result*, stable across attempts and leases of one run."""

    request = manifest.request
    return canonical_hash(
        {
            "run_id": request.context.run_id,
            "revision": request.context.revision,
            "plan_hash": plan_hash(request.plan),
            "inputs": [[item.uri, item.format, item.version] for item in request.inputs],
            "output": [request.output.uri, request.output.format],
        }
    )


def table_identifier(uri: str, *, catalog: str = DEFAULT_SPARK_CATALOG) -> str:
    """``iceberg://ns.table`` → ``<catalog>.ns.table`` with strict identifier parts."""

    parsed = urlsplit(uri)
    if parsed.scheme != "iceberg" or parsed.query or parsed.fragment:
        raise FatalError("Iceberg artifacts must use iceberg://namespace.table")
    dotted = (parsed.netloc + parsed.path.replace("/", ".")).strip(".")
    parts = dotted.split(".")
    if len(parts) < 2 or not all(_IDENTIFIER_PART.fullmatch(part) for part in parts):
        raise FatalError("Iceberg identifier must be namespace.table with plain identifiers")
    if not _IDENTIFIER_PART.fullmatch(catalog):
        raise FatalError("Spark catalog name must be a plain identifier")
    return ".".join([catalog, *parts])


class IcebergOutputCommitter:
    capabilities = ICEBERG_COMMIT_CAPABILITIES

    def __init__(
        self,
        spark: Any,
        functions: Any,
        *,
        catalog: str = DEFAULT_SPARK_CATALOG,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._spark = spark
        self._functions = functions
        self._catalog = catalog
        self._clock = clock

    def commit(self, frame: Any, manifest: RunManifest) -> OutputProof:
        request = manifest.request
        if request.output.format != "iceberg":
            raise FatalError("IcebergOutputCommitter only publishes iceberg:// outputs")
        table = table_identifier(request.output.uri, catalog=self._catalog)
        started_at = self._clock()
        schema_hash = canonical_hash({"spark_schema": frame.schema.jsonValue()})

        existing = self._run_snapshot(table, manifest)
        if existing is None:
            self._refuse_if_stale(table, manifest)
            self._write(frame, table, manifest)
            existing = self._run_snapshot(table, manifest)
            if existing is None:
                raise FatalError(
                    "Iceberg commit finished but no snapshot carries this run's identity"
                )
        snapshot_id, summary = existing
        version = str(snapshot_id)
        return OutputProof(
            run_id=request.context.run_id,
            revision=request.context.revision,
            output=replace(request.output, version=version),
            input_versions=tuple(str(item.version) for item in request.inputs),
            row_count=_int_or_none(summary.get("total-records")),
            schema_hash=schema_hash,
            plan_hash=plan_hash(request.plan),
            started_at=started_at,
            finished_at=max(self._clock(), started_at),
            output_version=version,
        )

    def _snapshots(self, table: str) -> list[tuple[int, datetime, Mapping[str, str]]]:
        if not self._table_exists(table):
            return []
        rows = self._spark.sql(
            f"SELECT snapshot_id, committed_at, summary FROM {table}.snapshots "
            "ORDER BY committed_at DESC"
        ).collect()
        return [
            (int(row["snapshot_id"]), row["committed_at"], dict(row["summary"] or {}))
            for row in rows
        ]

    def _run_snapshot(
        self, table: str, manifest: RunManifest
    ) -> tuple[int, Mapping[str, str]] | None:
        run_id = manifest.request.context.run_id
        for snapshot_id, _committed_at, summary in self._snapshots(table):
            if summary.get(RUN_ID_PROPERTY) != run_id:
                continue
            if summary.get(LOGICAL_HASH_PROPERTY) != logical_hash(manifest):
                raise FatalError(
                    "an Iceberg snapshot for this run id was produced from a different request"
                )
            return snapshot_id, summary
        return None

    def _refuse_if_stale(self, table: str, manifest: RunManifest) -> None:
        snapshots = self._snapshots(table)
        if not snapshots:
            return
        published = snapshots[0][2].get(MANIFEST_CREATED_PROPERTY)
        if published is not None and published > _utc_iso(manifest.created_at):
            raise StaleRunError(
                "a newer Ballista run already published this table; refusing stale attempt"
            )

    def _write(self, frame: Any, table: str, manifest: RunManifest) -> None:
        writer = frame.writeTo(table)
        for key, value in (
            (RUN_ID_PROPERTY, manifest.request.context.run_id),
            (MANIFEST_HASH_PROPERTY, manifest.content_hash),
            (LOGICAL_HASH_PROPERTY, logical_hash(manifest)),
            (MANIFEST_CREATED_PROPERTY, _utc_iso(manifest.created_at)),
        ):
            writer = writer.option(f"snapshot-property.{key}", value)
        if self._table_exists(table):
            writer.overwrite(self._functions.lit(True))
        else:
            writer.using("iceberg").create()

    def _table_exists(self, table: str) -> bool:
        return bool(self._spark.catalog.tableExists(table))


class SnapshotInputReader:
    """Reads exactly the input versions recorded in the manifest.

    * ``iceberg://`` inputs are read at the recorded snapshot id.
    * Parquet inputs must carry a listing fingerprint; the reader recomputes it
      and refuses to run if the files changed since planning.
    """

    def __init__(
        self,
        *,
        catalog: str = DEFAULT_SPARK_CATALOG,
        fingerprint: Callable[[str], str] | None = None,
    ) -> None:
        self._catalog = catalog
        self._fingerprint = fingerprint

    def read(self, spark: Any, inputs: Sequence[ArtifactRef]) -> Any:
        if len(inputs) != 1:
            raise FatalError("Ballista v1 portable plans read exactly one input artifact")
        artifact = inputs[0]
        if not artifact.version:
            raise FatalError("Spark worker refuses unversioned input artifacts")
        if artifact.format == "iceberg":
            table = table_identifier(artifact.uri, catalog=self._catalog)
            return spark.read.option("snapshot-id", int(artifact.version)).table(table)
        if artifact.format == "parquet":
            if self._fingerprint is None:
                raise FatalError("Parquet inputs need a listing fingerprint function")
            if self._fingerprint(artifact.uri) != artifact.version:
                raise FatalError("Parquet input changed since the run was planned")
            return spark.read.parquet(_spark_path(artifact.uri))
        raise FatalError(f"unsupported Ballista input format: {artifact.format}")


def _spark_path(uri: str) -> str:
    # Hadoop's S3A connector is the Spark-side client for s3:// object paths.
    return "s3a://" + uri[len("s3://") :] if uri.startswith("s3://") else uri


def _utc_iso(value: datetime) -> str:
    """Fixed-offset ISO text so lexical order equals time order."""
    return value.astimezone(UTC).isoformat()


def _int_or_none(value: str | None) -> int | None:
    try:
        return None if value is None else int(value)
    except ValueError:
        return None


__all__ = [
    "DEFAULT_SPARK_CATALOG",
    "ICEBERG_COMMIT_CAPABILITIES",
    "MANIFEST_CREATED_PROPERTY",
    "MANIFEST_HASH_PROPERTY",
    "RUN_ID_PROPERTY",
    "LOGICAL_HASH_PROPERTY",
    "IcebergOutputCommitter",
    "StaleRunError",
    "SnapshotInputReader",
    "logical_hash",
    "table_identifier",
]
