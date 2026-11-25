"""Split a transfer into units: one half-open range of the split column each.

A unit is the step that is exported, loaded and marked done together. Its id is
derived from the transfer name, the query, the target and the range, so a rerun
finds the same units, and a changed query or target starts over with new ones
instead of skipping ranges that were loaded under the old settings.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass

from arsenal_core.state import UnitSpec
from pugio.transfer.spec import TransferSpec, TransferSplitSpec

Bound = dt.date | int


@dataclass(frozen=True)
class TransferUnit:
    unit: UnitSpec
    uri: str  # s3://.../<name>/<unit_id>/ — only this unit's files live here
    predicate: str | None  # SQL condition shared by the export query and the replace delete
    lo: Bound | None = None
    hi: Bound | None = None

    def query(self, base: str) -> str:
        if self.predicate is None:
            return base
        # Newlines keep a trailing `-- comment` in the user's query from eating the range.
        return f"SELECT * FROM (\n{base}\n) arsenal_src WHERE {self.predicate}"


def sql_literal(value: Bound) -> str:
    """Literals both Hive and Redshift read the same way. Values come from the spec, not data."""

    if isinstance(value, dt.date):
        return f"DATE '{value.isoformat()}'"
    return str(value)


def range_predicate(column: str, lo: Bound, hi: Bound) -> str:
    return f"{column} >= {sql_literal(lo)} AND {column} < {sql_literal(hi)}"


def _next_month(day: dt.date) -> dt.date:
    return dt.date(day.year + day.month // 12, day.month % 12 + 1, 1)


def _ranges(split: TransferSplitSpec) -> Iterator[tuple[Bound, Bound]]:
    lo, end = split.start, split.end
    while lo < end:  # pyright: ignore[reportOperatorIssue]
        if isinstance(lo, dt.date):
            assert isinstance(end, dt.date)
            hi: Bound = lo + dt.timedelta(days=1) if split.every == "day" else _next_month(lo)
            hi = min(hi, end)
        else:
            assert isinstance(end, int) and isinstance(split.every, int)
            hi = min(lo + split.every, end)
        yield lo, hi
        lo = hi


def _source_id(spec: TransferSpec) -> str:
    """What the units' results depend on: the query and where and how they are loaded."""

    target = spec.target
    identity = json.dumps(
        [spec.source.query, target.dsn_env, target.table, target.mode, target.merge_key]
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"{spec.source.engine}:{digest}"


def plan_units(spec: TransferSpec) -> list[TransferUnit]:
    source = _source_id(spec)
    prefix = f"{spec.staging_path}/{spec.name}"

    def make(key: str, predicate: str | None, lo: Bound | None, hi: Bound | None) -> TransferUnit:
        payload = {"lo": str(lo), "hi": str(hi)} if lo is not None else {}
        unit = UnitSpec.create(pipeline=spec.name, source=source, unit_key=key, payload=payload)
        return TransferUnit(unit, f"{prefix}/{unit.unit_id}/", predicate, lo, hi)

    split = spec.split
    if split is None:
        return [make("all", None, None, None)]
    return [
        make(f"{split.column}={lo}..{hi}", range_predicate(split.column, lo, hi), lo, hi)
        for lo, hi in _ranges(split)
    ]
