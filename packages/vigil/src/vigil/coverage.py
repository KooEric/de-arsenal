"""Coverage: which techniques have rules, which have none, and whether the data rules need is there.

`CoverageReport` is the stdout JSON contract (schemas/vigil-coverage.json).
"""

from __future__ import annotations

import re
from datetime import datetime
from importlib import resources
from typing import Any, Final, Literal

import duckdb
import yaml
from pydantic import BaseModel, ConfigDict

from vigil.events import EVENT_COLUMNS
from vigil.rule import Rule

_TECHNIQUES_FILE: Final = "cloudtrail-techniques.yaml"
_RATIO_DIGITS: Final = 4


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Technique(_Frozen):
    id: str
    name: str


class TechniqueCoverage(_Frozen):
    id: str
    name: str
    rules: list[str]
    tests: int


class SourceCoverage(_Frozen):
    source: str
    rules: int
    last_event_time: datetime | None


class ColumnHealth(_Frozen):
    column: str
    used_by: list[str]
    null_ratio: float | None


class CoverageReport(_Frozen):
    schema_version: Literal[1] = 1
    rules: int
    techniques: list[TechniqueCoverage]
    uncovered: list[str]
    unlisted: list[str]
    sources: list[SourceCoverage]
    columns: list[ColumnHealth]


def load_techniques() -> list[Technique]:
    text = (resources.files("vigil") / "data" / _TECHNIQUES_FILE).read_text(encoding="utf-8")
    raw: list[dict[str, Any]] = yaml.safe_load(text)
    return [Technique.model_validate(item) for item in raw]


def build_coverage(rules: list[Rule], con: duckdb.DuckDBPyConnection | None) -> CoverageReport:
    """`con` is a connection from `vigil.events.load_events`, or None to read rule files only."""
    known = load_techniques()
    techniques = [_technique_coverage(technique, rules) for technique in known]
    known_ids = {technique.id for technique in known}
    used_ids = {technique_id for rule in rules for technique_id in rule.attack}
    return CoverageReport(
        rules=len(rules),
        techniques=techniques,
        uncovered=[item.id for item in techniques if not item.rules],
        unlisted=sorted(used_ids - known_ids),
        sources=_sources(rules, con),
        columns=_columns(rules, con),
    )


def _technique_coverage(technique: Technique, rules: list[Rule]) -> TechniqueCoverage:
    covering = [rule for rule in rules if technique.id in rule.attack]
    tests = sum(len(rule.tests.match) + len(rule.tests.no_match) for rule in covering)
    return TechniqueCoverage(
        id=technique.id, name=technique.name, rules=[rule.id for rule in covering], tests=tests
    )


def _sources(rules: list[Rule], con: duckdb.DuckDBPyConnection | None) -> list[SourceCoverage]:
    result: list[SourceCoverage] = []
    for source in sorted({rule.source for rule in rules}):
        last: datetime | None = None
        if con is not None:
            row = con.execute(
                "SELECT max(event_time) FROM events WHERE source = ?", [source]
            ).fetchone()
            last = row[0] if row else None
        count = sum(1 for rule in rules if rule.source == source)
        result.append(SourceCoverage(source=source, rules=count, last_event_time=last))
    return result


def _columns(rules: list[Rule], con: duckdb.DuckDBPyConnection | None) -> list[ColumnHealth]:
    """Columns at least one rule names in its query. Matching is by whole word in the SQL text."""
    result: list[ColumnHealth] = []
    for column in EVENT_COLUMNS:
        word = re.compile(rf"\b{column}\b")
        used_by = [rule.id for rule in rules if word.search(rule.query)]
        if not used_by:
            continue
        result.append(
            ColumnHealth(column=column, used_by=used_by, null_ratio=_null_ratio(con, column))
        )
    return result


def _null_ratio(con: duckdb.DuckDBPyConnection | None, column: str) -> float | None:
    if con is None:
        return None
    row = con.execute(f"SELECT count(*), count({column}) FROM events").fetchone()
    if row is None or row[0] == 0:
        return None
    return round(1 - row[1] / row[0], _RATIO_DIGITS)
