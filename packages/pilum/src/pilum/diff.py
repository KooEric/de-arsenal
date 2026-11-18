"""Read the source and work out what the destination has not been told yet."""

from __future__ import annotations

import base64
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

import duckdb
import pyarrow as pa

from arsenal_core.errors import FatalError
from arsenal_core.execution.canonical import canonical_hash, canonical_json
from pilum.spec import DispatchSpec, SourceSpec

ActionKind = Literal["insert", "update", "delete"]


def jsonable(value: Any) -> Any:
    """A deterministic JSON-safe form of one Arrow value (also what gets sent)."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {str(key): jsonable(item) for key, item in mapping.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in cast(list[object], value)]
    raise FatalError(f"cannot dispatch a value of type {type(value).__name__}")


def _fetch(con: duckdb.DuckDBPyConnection, sql: str, params: list[str]) -> pa.Table:
    relation = con.sql(sql, params=params) if params else con.sql(sql)
    return cast(pa.Table, relation.to_arrow_table())  # pyright: ignore[reportUnknownMemberType]


def read_source(source: SourceSpec) -> list[dict[str, Any]]:
    """Load the whole source as JSON-safe records."""

    con = duckdb.connect()
    try:
        if source.type == "parquet":
            assert source.path is not None
            target = source.path
            if Path(target).is_dir():
                target = f"{target.rstrip('/')}/**/*.parquet"
            try:
                table = _fetch(con, "SELECT * FROM read_parquet(?, union_by_name=true)", [target])
            except duckdb.IOException as error:
                raise FatalError(
                    f"no Parquet files at source.path '{source.path}'; "
                    "run the pipeline that produces it first"
                ) from error
        else:
            assert source.sql is not None
            table = _fetch(con, source.sql, [])
    except duckdb.Error as error:
        raise FatalError(f"cannot read dispatch source: {error}") from error
    finally:
        con.close()
    rows = cast(list[dict[str, Any]], table.to_pylist())
    return [{name: jsonable(value) for name, value in row.items()} for row in rows]


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    record_key: str  # canonical JSON of {key column: value}
    content_hash: str | None  # None for a delete
    dispatch_id: str
    record: dict[str, Any]  # the full record; key columns only for a delete


@dataclass
class DispatchPlan:
    actions: list[Action] = field(default_factory=lambda: [])
    unchanged: int = 0
    dead: int = 0  # parked in the DLQ, skipped until `pilum replay`
    source_rows: int = 0
    ledger_size: int = 0

    def count(self, kind: ActionKind) -> int:
        return sum(1 for action in self.actions if action.kind == kind)

    @property
    def first_dispatch(self) -> bool:
        return self.ledger_size == 0

    def summary(self) -> dict[str, int]:
        return {
            "insert": self.count("insert"),
            "update": self.count("update"),
            "delete": self.count("delete"),
            "unchanged": self.unchanged,
            "dead": self.dead,
            "source_rows": self.source_rows,
            "ledger_size": self.ledger_size,
        }


def dispatch_id(name: str, record_key: str, content: str) -> str:
    """PL-ADR-1: same dispatch, same record, same content → same id, on every run.

    The destination URL and headers are deliberately not part of it: rotating a
    token or moving an endpoint must not make already-sent records look new.
    """

    return canonical_hash({"dispatch": name, "key": record_key, "content": content})[:32]


def build_plan(
    spec: DispatchSpec,
    rows: list[dict[str, Any]],
    ledger: dict[str, str],
    dead_ids: set[str],
) -> DispatchPlan:
    if rows:
        missing = sorted(spec.referenced_fields() - set(rows[0]))
        if missing:
            raise FatalError(
                f"dispatch '{spec.name}' refers to columns the source does not have: "
                f"{missing} (source columns: {sorted(rows[0])})"
            )
    plan = DispatchPlan(source_rows=len(rows), ledger_size=len(ledger))
    seen: set[str] = set()
    for row in rows:
        key_values = {column: row[column] for column in spec.key}
        if any(value is None for value in key_values.values()):
            raise FatalError(f"dispatch '{spec.name}': null in key {spec.key}: {key_values}")
        record_key = canonical_json(key_values)
        if record_key in seen:
            raise FatalError(
                f"dispatch '{spec.name}': key {key_values} appears more than once in the "
                "source; which version to send would be arbitrary"
            )
        seen.add(record_key)
        content = canonical_hash(row)
        known = ledger.get(record_key)
        if known == content:
            plan.unchanged += 1
            continue
        if known is not None and spec.mode == "insert":
            plan.unchanged += 1  # insert mode never re-sends a known key
            continue
        action = Action(
            kind="insert" if known is None else "update",
            record_key=record_key,
            content_hash=content,
            dispatch_id=dispatch_id(spec.name, record_key, content),
            record=row,
        )
        _add(plan, action, dead_ids)
    if spec.mode == "mirror":
        for record_key in sorted(set(ledger) - seen):
            action = Action(
                kind="delete",
                record_key=record_key,
                content_hash=None,
                dispatch_id=dispatch_id(spec.name, record_key, f"delete:{ledger[record_key]}"),
                record=json.loads(record_key),
            )
            _add(plan, action, dead_ids)
    return plan


def _add(plan: DispatchPlan, action: Action, dead_ids: set[str]) -> None:
    if action.dispatch_id in dead_ids:
        plan.dead += 1
    else:
        plan.actions.append(action)
