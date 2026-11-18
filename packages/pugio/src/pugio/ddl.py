"""DDL for query engines that read a pipeline's Parquet output in place.

``pugio ddl`` prints a ``CREATE TABLE`` for a Trino Hive external table over
an S3/GCS Parquet sink, from the schema the pipeline last recorded. Nothing is
executed: the statement is printed for the operator to run in Trino.
"""

from __future__ import annotations

import json
import re

from arsenal_core.errors import FatalError

_DECIMAL = re.compile(r"^decimal\d*\((\d+),\s*(-?\d+)\)$")
_SIMPLE = {
    "int8": "TINYINT",
    "int16": "SMALLINT",
    "int32": "INTEGER",
    "int64": "BIGINT",
    "uint8": "SMALLINT",
    "uint16": "INTEGER",
    "uint32": "BIGINT",
    "float": "REAL",
    "double": "DOUBLE",
    "bool": "BOOLEAN",
    "string": "VARCHAR",
    "large_string": "VARCHAR",
    "binary": "VARBINARY",
    "large_binary": "VARBINARY",
    "date32[day]": "DATE",
    "null": "VARCHAR",  # an all-empty column: readable as text whatever arrives later
}


def trino_type(arrow_type: str, column: str) -> str:
    """Map the Arrow type string stored in a schema snapshot to a Trino type."""

    if arrow_type in _SIMPLE:
        return _SIMPLE[arrow_type]
    if arrow_type.startswith("timestamp["):
        return "TIMESTAMP(6) WITH TIME ZONE" if "tz=" in arrow_type else "TIMESTAMP(6)"
    match = _DECIMAL.match(arrow_type)
    if match:
        return f"DECIMAL({match.group(1)}, {match.group(2)})"
    raise FatalError(f"column {column!r}: no Trino type for Arrow type {arrow_type!r}")


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def trino_hive_external_table(
    *, schema_json: str, location: str, catalog: str, schema: str, table: str
) -> str:
    """``CREATE TABLE ... WITH (external_location, format = 'PARQUET')``."""

    columns = json.loads(schema_json)
    if not columns:
        raise FatalError("the recorded schema has no columns")
    lines = [
        f"    {_ident(column['name'])} {trino_type(column['type'], column['name'])}"
        for column in columns
    ]
    name = ".".join(_ident(part) for part in (catalog, schema, table))
    return (
        f"CREATE TABLE IF NOT EXISTS {name} (\n"
        + ",\n".join(lines)
        + "\n)\nWITH (\n"
        + f"    external_location = {_literal(location.rstrip('/') + '/')},\n"
        + "    format = 'PARQUET'\n);"
    )
