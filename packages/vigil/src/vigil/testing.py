"""Run each rule against its own fixture cases. A case runs alone in a fresh `events`."""

from __future__ import annotations

from vigil.events import fixture_events
from vigil.rule import Case, Rule


def check_rule(rule: Rule) -> list[str]:
    """Failure messages for one rule; an empty list means it passes."""
    failures: list[str] = []
    if not rule.tests.match:
        failures.append("no match case (tests.match is empty)")
    if not rule.tests.no_match:
        failures.append("no no_match case (tests.no_match is empty)")
    for index, case in enumerate(rule.tests.match):
        if _hits(rule, case) == 0:
            failures.append(f"match[{index}]: expected a detection, got none")
    for index, case in enumerate(rule.tests.no_match):
        hits = _hits(rule, case)
        if hits:
            failures.append(f"no_match[{index}]: expected no detection, got {hits}")
    return failures


def check_rules(rules: list[Rule]) -> dict[str, list[str]]:
    return {rule.id: check_rule(rule) for rule in rules}


def _hits(rule: Rule, case: Case) -> int:
    rows = case if isinstance(case, list) else [case]
    con = fixture_events(rows)
    try:
        return len(con.execute(rule.query).fetchall())
    finally:
        con.close()
