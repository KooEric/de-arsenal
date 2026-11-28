"""Run each rule against its own fixture cases. A case runs alone in a fresh `events`."""

from __future__ import annotations

import duckdb

from arsenal_core.errors import FatalError
from vigil.events import fixture_events
from vigil.rule import Case, Rule, detection_sql


def check_rule(rule: Rule) -> list[str]:
    """Failure messages for one rule; an empty list means it passes."""
    failures: list[str] = []
    if not rule.tests.match:
        failures.append("no match case (tests.match is empty)")
    if not rule.tests.no_match:
        failures.append("no no_match case (tests.no_match is empty)")
    for index, case in enumerate(rule.tests.match):
        label = f"match[{index}]"
        hits, error = _run_case(rule, label, case)
        if error:
            failures.append(error)
        elif hits == 0:
            failures.append(f"{label}: expected a detection, got none")
    for index, case in enumerate(rule.tests.no_match):
        label = f"no_match[{index}]"
        hits, error = _run_case(rule, label, case)
        if error:
            failures.append(error)
        elif hits:
            failures.append(f"{label}: expected no detection, got {hits}")
    return failures


def check_rules(rules: list[Rule]) -> dict[str, list[str]]:
    return {rule.id: check_rule(rule) for rule in rules}


def _run_case(rule: Rule, label: str, case: Case) -> tuple[int, str | None]:
    """(hit count, None), or (0, failure message) when DuckDB rejects the case or the query."""
    rows = case if isinstance(case, list) else [case]
    try:
        con = fixture_events(rows)
    except FatalError as e:
        raise FatalError(f"rule {rule.id!r} {label}: {e}") from e
    except duckdb.Error as e:
        return 0, f"{label}: error running case: {e}"
    try:
        return len(con.execute(detection_sql(rule.query)).fetchall()), None
    except duckdb.Error as e:
        return 0, f"{label}: error running case: {e}"
    finally:
        con.close()
