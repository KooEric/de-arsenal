"""Detection rule files. One YAML file is one rule; every rule is validated on load."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final, Literal

import duckdb
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from arsenal_core.errors import FatalError
from vigil.events import empty_events

Severity = Literal["low", "medium", "high", "critical"]
SEVERITY_ORDER: Final[tuple[Severity, ...]] = ("low", "medium", "high", "critical")

Event = dict[str, Any]
# One event, or several events that must be looked at together (correlation rules).
Case = Event | list[Event]

_TECHNIQUE: Final = re.compile(r"T\d{4}(\.\d{3})?")


class RuleTests(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    match: list[Case] = Field(default_factory=lambda: list[Case]())
    no_match: list[Case] = Field(default_factory=lambda: list[Case]())


class Rule(BaseModel):
    """The rule file contract (schemas/vigil-rule.json)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1)
    severity: Severity
    attack: list[str] = Field(min_length=1)
    source: str = Field(min_length=1)
    query: str
    tests: RuleTests = Field(default_factory=RuleTests)

    @field_validator("attack")
    @classmethod
    def _technique_ids(cls, value: list[str]) -> list[str]:
        bad = [item for item in value if not _TECHNIQUE.fullmatch(item)]
        if bad:
            raise ValueError(f"not ATT&CK technique ids (T1234 or T1234.001): {bad}")
        return value

    @field_validator("query")
    @classmethod
    def _one_select(cls, value: str) -> str:
        text = _drop_final_semicolon(value.strip())
        try:
            statements = duckdb.extract_statements(text)
        except duckdb.Error as e:
            raise ValueError(f"does not parse: {e}") from e
        if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
            raise ValueError("must be exactly one SELECT statement")
        return text


def detection_sql(query: str) -> str:
    """The statement that lists detected events for a rule query.

    A detection is a row whose `event_id` is an event in `events`. `vigil test` and
    `vigil run` both count detections with this statement, so they cannot disagree.
    """
    return (
        "SELECT DISTINCT e.event_id, e.event_time, e.actor_id "
        f"FROM ({query}\n) AS q JOIN events AS e ON e.event_id = q.event_id "
        "ORDER BY e.event_time, e.event_id"
    )


def _drop_final_semicolon(text: str) -> str:
    """Remove a statement-ending `;` so the query can be embedded as a subquery.

    Comments after the `;` stay in the text. DuckDB's tokenizer skips comments and keeps
    `;` inside strings as part of the string token, so the last token tells the truth.
    """
    tokens = duckdb.tokenize(text)
    if not tokens:
        return text
    offset, kind = tokens[-1]
    if kind == duckdb.token_type.operator and text[offset] == ";":
        return (text[:offset] + text[offset + 1 :]).strip()
    return text


def load_rules(rules_dir: Path) -> list[Rule]:
    if not rules_dir.is_dir():
        raise FatalError(f"rules directory not found: {rules_dir}")
    paths = sorted([*rules_dir.glob("*.yaml"), *rules_dir.glob("*.yml")], key=lambda p: p.name)
    if not paths:
        raise FatalError(f"no rule files (*.yaml, *.yml) in {rules_dir}")
    rules: list[Rule] = []
    seen: dict[str, Path] = {}
    for path in paths:
        rule = _load_one(path)
        if rule.id in seen:
            raise FatalError(f"duplicate rule id {rule.id!r}: {seen[rule.id]} and {path}")
        seen[rule.id] = path
        rules.append(rule)
    return rules


def _load_one(path: Path) -> Rule:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise FatalError(f"{path}: cannot read rule: {e}") from e
    try:
        rule = Rule.model_validate(raw)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'rule'}: {error['msg']}"
            for error in e.errors()
        )
        raise FatalError(f"{path}: {problems}") from e
    _check_against_events(path, rule)
    return rule


def _check_against_events(path: Path, rule: Rule) -> None:
    """Run the query on an empty `events`: catches unknown columns and a missing event_id."""
    con = empty_events()
    try:
        description = con.execute(f"SELECT * FROM ({rule.query}\n) LIMIT 0").description
    except duckdb.Error as e:
        raise FatalError(f"{path}: query fails against events: {e}") from e
    finally:
        con.close()
    names = [str(column[0]) for column in description or []]
    if "event_id" not in names:
        raise FatalError(f"{path}: query must return event_id (returns: {names})")
