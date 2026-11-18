"""Apache Iceberg sink (experimental), written with pyiceberg.

One Iceberg commit per batch. The table is created from the first batch's
schema, and columns that appear later are added to it.

- With ``merge_key``: rows are upserted on the key, so writing a batch again
  leaves the same rows.
- Without ``merge_key``: the batch is appended, and its snapshot is tagged with
  the unit id. A batch whose unit id is already in the table's snapshot history
  is not appended again. Expiring snapshots removes that memory.

Trino, Spark, Athena and Snowflake read the result through the same catalog.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pyarrow as pa

from arsenal_core.errors import ArsenalError, FatalError, RetryableError
from arsenal_core.spec.models import IcebergSinkSpec
from arsenal_core.state import UnitSpec
from pugio.sinks.contracts import ICEBERG_CAPABILITIES
from pugio.sinks.redshift import keep_last_per_key

UNIT_ID_PROPERTY = "de-arsenal.unit-id"
_RETRYABLE_ERROR_NAMES = frozenset(
    {"CommitFailedException", "CommitStateUnknownException", "ServiceUnavailableError"}
)

LoadCatalog = Callable[[], Any]


def normalize(batch: pa.RecordBatch) -> pa.Table:
    """Arrow types Iceberg has no direct form for.

    An all-empty column (type ``null``) becomes a string column; timestamps in
    seconds or milliseconds become microseconds, Iceberg's unit.
    """

    arrays: list[Any] = []
    for column in batch.columns:
        kind = column.type
        if pa.types.is_null(kind):
            column = column.cast(pa.string())
        elif pa.types.is_timestamp(kind) and kind.unit in {"s", "ms"}:
            column = column.cast(pa.timestamp("us", tz=kind.tz))
        arrays.append(column)
    return pa.Table.from_arrays(arrays, names=batch.schema.names)


class IcebergSink:
    capabilities = ICEBERG_CAPABILITIES

    def __init__(self, spec: IcebergSinkSpec, *, load_catalog: LoadCatalog | None = None) -> None:
        self._spec = spec
        self._load_catalog = load_catalog or self._default_catalog
        namespace, _, name = spec.table.rpartition(".")
        self._namespace = tuple(namespace.split("."))
        self._identifier = (*self._namespace, name)

    def _default_catalog(self) -> Any:
        try:
            from pyiceberg.catalog import load_catalog
        except ImportError as error:
            raise FatalError(
                "iceberg sink requires the 'iceberg' extra: pip install pugio[iceberg]"
            ) from error
        properties = dict(self._spec.catalog)
        # A SQL catalog stores its own name with every table, so readers must use the
        # same one. `name` in the catalog block sets it; REST and Glue ignore it.
        name = properties.pop("name", "arsenal")
        return load_catalog(name, **properties)

    def write(self, unit: UnitSpec, batch: pa.RecordBatch) -> None:
        if self._spec.merge_key:
            batch = keep_last_per_key(batch, self._spec.merge_key)
        data = normalize(batch)
        try:
            table = self._open(data.schema)
            properties = {UNIT_ID_PROPERTY: unit.unit_id}
            if self._spec.merge_key:
                table.upsert(
                    data, join_cols=list(self._spec.merge_key), snapshot_properties=properties
                )
            elif not self._already_appended(table, unit.unit_id):
                table.append(data, snapshot_properties=properties)
        except ArsenalError:
            raise
        except Exception as error:
            names = {cls.__name__ for cls in type(error).__mro__}
            kind = RetryableError if names & _RETRYABLE_ERROR_NAMES else FatalError
            raise kind(
                f"iceberg write to {self._spec.table} failed for unit {unit.unit_id}: "
                f"{type(error).__name__}: {error}"
            ) from error

    def _open(self, schema: pa.Schema) -> Any:
        catalog = self._load_catalog()
        catalog.create_namespace_if_not_exists(self._namespace)
        table = catalog.create_table_if_not_exists(self._identifier, schema=schema)
        known = {field.name for field in table.schema().fields}
        if any(name not in known for name in schema.names):
            with table.update_schema() as update:
                update.union_by_name(schema)
        return table

    @staticmethod
    def _already_appended(table: Any, unit_id: str) -> bool:
        return any(
            snapshot.summary is not None and snapshot.summary.get(UNIT_ID_PROPERTY) == unit_id
            for snapshot in table.snapshots()
        )
