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
from gladius.plan import (
    Cast,
    ColumnRename,
    Deduplicate,
    Derive,
    Filter,
    Project,
    Rename,
    Select,
)


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
                current = _rename_like_duckdb(current, columns)
            case Cast(columns=columns):
                for item in columns:
                    current = current.withColumn(
                        item.column,
                        _cast_like_duckdb(current, item.column, item.data_type, functions),
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


_INTEGRAL_TARGETS = frozenset({"integer", "bigint"})


def _rename_like_duckdb(frame: Any, columns: Sequence[ColumnRename]) -> Any:
    """DuckDB renders rename as ``* EXCLUDE (old), old AS new``: renamed
    columns move to the end in rename order. Spark keeps them in place, so
    reorder explicitly to keep the output schema identical (invariant I7)."""

    renamed = {item.source: item.target for item in columns}
    for item in columns:
        frame = frame.withColumnRenamed(item.source, item.target)
    original = list(frame.columns)
    kept = [name for name in original if name not in renamed.values()]
    return frame.select(*kept, *(item.target for item in columns))


def _cast_like_duckdb(frame: Any, column: str, data_type: str, functions: Any) -> Any:
    """Spark truncates fractional values cast to integers; DuckDB rounds.

    DuckDB rounds DOUBLE/FLOAT half-to-even and DECIMAL half-away-from-zero,
    which are Spark's ``bround`` and ``round`` respectively.
    """

    target = _spark_type(data_type)
    source = functions.col(column)
    if data_type.lower() in _INTEGRAL_TARGETS:
        kind = _column_type_name(frame, column)
        if kind in {"double", "float"}:
            source = functions.bround(source, 0)
        elif kind == "decimal":
            source = functions.round(source, 0)
    return source.cast(target)


def _column_type_name(frame: Any, column: str) -> str:
    return str(frame.schema[column].dataType.typeName())


def _spark_type(data_type: str) -> str:
    normalized = data_type.lower()
    return {
        "bigint": "long",
        "boolean": "boolean",
        "date": "date",
        # Gladius renders portable DECIMAL as DECIMAL(38, 9); Spark's bare
        # DECIMAL is DECIMAL(10,0) and would silently drop the fraction.
        "decimal": "decimal(38,9)",
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
