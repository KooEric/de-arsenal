"""Check the query's columns against the target table before anything is exported.

Redshift's ``COPY ... FORMAT AS PARQUET`` loads columns by position, and Hive's
exported Parquet has no real column names (``_col0``, ``_col1``, …). If the SELECT
list and the target table disagree on order, two columns of the same type swap
without an error. Comparing names position by position catches that up front.

Types are compared by family (integer, float, decimal, string, boolean, date,
timestamp, timestamptz), not by size: ``INT`` into ``bigint`` or ``STRING`` into
``varchar(32)`` pass, ``DOUBLE`` into ``numeric(18,2)`` does not. Sizes and lengths
are left to ``COPY``, which fails the unit on an overflow.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from itertools import zip_longest

from arsenal_core.errors import FatalError

logger = logging.getLogger("pugio.transfer")

_MAX_SHOWN = 10

# (source family, target family) pairs that load without losing meaning.
_WIDENINGS = frozenset({("integer", "decimal")})

_HIVE_FAMILIES = {
    "TINYINT": "integer",
    "SMALLINT": "integer",
    "INT": "integer",
    "INTEGER": "integer",
    "BIGINT": "integer",
    "FLOAT": "float",
    "DOUBLE": "float",
    "DOUBLE PRECISION": "float",
    "DECIMAL": "decimal",
    "NUMERIC": "decimal",
    "STRING": "string",
    "VARCHAR": "string",
    "CHAR": "string",
    "BOOLEAN": "boolean",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
    "TIMESTAMP WITH LOCAL TIME ZONE": "timestamptz",
}

# PostgreSQL type OIDs (redshift_connector's description type code) → (name, family).
_REDSHIFT_OIDS = {
    20: ("int8", "integer"),
    21: ("int2", "integer"),
    23: ("int4", "integer"),
    700: ("float4", "float"),
    701: ("float8", "float"),
    1700: ("numeric", "decimal"),
    18: ("char", "string"),
    25: ("text", "string"),
    1042: ("bpchar", "string"),
    1043: ("varchar", "string"),
    16: ("bool", "boolean"),
    1082: ("date", "date"),
    1114: ("timestamp", "timestamp"),
    1184: ("timestamptz", "timestamptz"),
}

_CATALOG_FAMILIES = {
    "smallint": "integer",
    "integer": "integer",
    "bigint": "integer",
    "real": "float",
    "double precision": "float",
    "numeric": "decimal",
    "character varying": "string",
    "character": "string",
    "text": "string",
    "boolean": "boolean",
    "date": "date",
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamptz",
}


@dataclass(frozen=True)
class Column:
    name: str
    type: str  # as the engine reports it, for messages
    family: str | None  # None: a type this check does not know


def from_hive(name: str, type_code: object) -> Column:
    label = str(type_code).upper().strip()
    base = re.sub(r"_TYPE$", "", re.sub(r"\(.*\)$", "", label)).strip()
    return Column(name, label, _HIVE_FAMILIES.get(base))


def from_redshift_oid(name: str, type_code: object) -> Column:
    known = _REDSHIFT_OIDS.get(type_code) if isinstance(type_code, int) else None
    if known is None:
        return Column(name, f"oid {type_code}", None)
    return Column(name, known[0], known[1])


def from_redshift_catalog(
    name: str,
    data_type: str,
    length: int | None,
    precision: int | None,
    scale: int | None,
) -> Column:
    label = data_type
    if length is not None:
        label = f"{data_type}({length})"
    elif data_type == "numeric" and precision is not None:
        label = f"numeric({precision},{scale or 0})"
    return Column(name, label, _CATALOG_FAMILIES.get(data_type))


def _unaliased(name: str) -> bool:
    """Names engines give expressions: Hive ``_c1``, Redshift ``?column?``."""

    return name == "?column?" or (name.startswith("_c") and name[2:].isdigit())


def _cut(lines: list[str]) -> list[str]:
    if len(lines) <= _MAX_SHOWN:
        return lines
    return [*lines[:_MAX_SHOWN], f"  … and {len(lines) - _MAX_SHOWN} more"]


def _check_names(query: list[Column], target: list[Column], table: str) -> None:
    mismatches = [
        (position, ours, theirs)
        for position, (ours, theirs) in enumerate(zip_longest(query, target), start=1)
        if ours is None or theirs is None or ours.name.lower() != theirs.name.lower()
    ]
    if not mismatches:
        return

    def show(column: Column | None) -> str:
        return "(none)" if column is None else repr(column.name)

    lines = [
        f"  position {position}: query {show(ours)}, table {show(theirs)}"
        for position, ours, theirs in mismatches
    ]
    hint = (
        "Write the SELECT list in the table's column order and alias each column to the "
        "table's name (`expr AS name`)"
    )
    if any(ours is not None and _unaliased(ours.name) for _, ours, _ in mismatches):
        hint += "; an expression without an alias gets a generated name"
    raise FatalError(
        f"the query's columns do not match {table} by position ({len(query)} in the query, "
        f"{len(target)} in the table). COPY loads Parquet columns by position, so this "
        "would put values in the wrong columns:\n"
        + "\n".join(_cut(lines))
        + f"\n{hint}. Set check_columns: false to skip this check"
    )


def _check_types(query: list[Column], target: list[Column], table: str) -> None:
    unknown: list[str] = []
    lines: list[str] = []
    for position, (ours, theirs) in enumerate(zip(query, target, strict=True), start=1):
        if ours.family is None or theirs.family is None:
            unknown.append(f"{theirs.name} (query {ours.type}, table {theirs.type})")
            continue
        if ours.family == theirs.family or (ours.family, theirs.family) in _WIDENINGS:
            continue
        lines.append(
            f"  position {position} {theirs.name!r}: query {ours.type} ({ours.family}), "
            f"table {theirs.type} ({theirs.family})"
        )
    if unknown:
        logger.warning("column types not checked for %s: %s", table, "; ".join(unknown))
    if lines:
        raise FatalError(
            f"the query's column types do not match {table}:\n"
            + "\n".join(_cut(lines))
            + "\nCast in the query (`CAST(expr AS type) AS name`) or change the table. "
            "Set check_types: false to compare names only"
        )


def compare_columns(
    query: list[Column], target: list[Column], *, table: str, check_types: bool = True
) -> None:
    """Raise unless both lists hold the same names in the same order (and type families)."""

    if not target:
        raise FatalError(
            f"target table {table} not found, or it has no columns visible to the target "
            "user. Create it before the first run"
        )
    _check_names(query, target, table)
    if check_types:
        _check_types(query, target, table)
