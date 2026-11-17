"""Small Spark-driver boundary for portable plans and transactional committers.

No Kubernetes client, coordinator state database, profile parser, or secret value
crosses this boundary. Cluster images provide concrete versioned readers and
atomic committers; the base package supplies only the deterministic renderer.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from arsenal_core.errors import FatalError
from arsenal_core.execution import ArtifactRef, OutputProof, SinkCapabilities
from ballista.artifacts import RunManifest
from ballista.plan import require_portable_mapping
from gladius.plan import Cast, Deduplicate, Derive, Filter, Project, Rename, Select


class SparkFunctions(Protocol):
    def expr(self, expression: str) -> Any: ...

    def col(self, column: str) -> Any: ...


class VersionedInputReader(Protocol):
    def read(self, spark: Any, inputs: Sequence[ArtifactRef]) -> Any: ...


class TransactionalOutputCommitter(Protocol):
    capabilities: SinkCapabilities

    def commit(self, frame: Any, manifest: RunManifest) -> OutputProof: ...


def apply_spark_plan(frame: Any, plan_mapping: Any, functions: SparkFunctions) -> Any:
    """Apply LogicalPlan operations using Spark's DataFrame API.

    Expressions are admitted only after Gladius' portable ANSI preflight and are
    passed through ``functions.expr``. Raw SQL and Python UDF nodes never reach
    this renderer.
    """

    plan = require_portable_mapping(plan_mapping)
    current = frame
    for operation in plan.operations:
        match operation:
            case Project(expressions=expressions):
                current = current.select(
                    *(
                        functions.expr(item.expression.source).alias(item.name)
                        for item in expressions
                    )
                )
            case Filter(predicate=predicate):
                current = current.filter(functions.expr(predicate.source))
            case Rename(columns=columns):
                for item in columns:
                    current = current.withColumnRenamed(item.source, item.target)
            case Cast(columns=columns):
                for item in columns:
                    current = current.withColumn(
                        item.column,
                        functions.col(item.column).cast(_spark_type(item.data_type)),
                    )
            case Derive(expressions=expressions):
                for item in expressions:
                    current = current.withColumn(item.name, functions.expr(item.expression.source))
            case Deduplicate(keys=keys):
                current = current.dropDuplicates(list(keys))
            case Select(columns=columns):
                current = current.select(*columns)
            case _:
                raise AssertionError(
                    f"portable preflight admitted unsupported node {type(operation).__name__}"
                )
    return current


def execute_manifest(
    manifest: RunManifest,
    *,
    spark: Any,
    functions: SparkFunctions,
    reader: VersionedInputReader,
    committer: TransactionalOutputCommitter,
) -> OutputProof:
    """Read exact input versions, render, and delegate one atomic publish."""

    if not all(item.version for item in manifest.request.inputs):
        raise FatalError("Spark worker refuses unversioned input artifacts")
    capabilities = committer.capabilities
    if not (
        capabilities.idempotent_batch_write
        and capabilities.atomic_publish
        and capabilities.concurrent_writers
    ):
        raise FatalError(
            "Spark worker requires an atomic/transactional, concurrent-writer-safe committer"
        )
    frame = reader.read(spark, manifest.request.inputs)
    rendered = apply_spark_plan(frame, manifest.request.plan, functions)
    proof = committer.commit(rendered, manifest)
    if proof.output_version is None:
        raise FatalError(
            "transactional committer did not return an output snapshot/transaction version"
        )
    return proof


def _spark_type(data_type: str) -> str:
    normalized = data_type.lower()
    return {
        "bigint": "long",
        "boolean": "boolean",
        "date": "date",
        "double": "double",
        "integer": "int",
        "timestamp": "timestamp",
        "varchar": "string",
    }.get(normalized, normalized)


__all__ = [
    "SparkFunctions",
    "TransactionalOutputCommitter",
    "VersionedInputReader",
    "apply_spark_plan",
    "execute_manifest",
]
