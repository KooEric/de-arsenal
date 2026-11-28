import pytest

from arsenal_core.errors import FatalError
from vigil.rule import Rule, RuleTests
from vigil.testing import check_rule

PAIR_QUERY = (
    "SELECT p.* FROM events p JOIN events k ON k.actor_id = p.actor_id "
    "WHERE k.action = 'CreateAccessKey' AND p.action = 'AttachUserPolicy' "
    "AND p.event_time >= k.event_time AND p.event_time <= k.event_time + INTERVAL 1 HOUR"
)


def _rule(query: str, tests: RuleTests) -> Rule:
    return Rule(
        id="r", title="t", severity="high", attack=["T1078.004"], source="cloudtrail",
        query=query, tests=tests,
    )  # fmt: skip


def test_passing_rule_has_no_failures() -> None:
    rule = _rule(
        "SELECT * FROM events WHERE actor_type = 'Root'",
        RuleTests(match=[{"actor_type": "Root"}], no_match=[{"actor_type": "IAMUser"}]),
    )
    assert check_rule(rule) == []


def test_missing_cases_fail() -> None:
    rule = _rule("SELECT * FROM events", RuleTests())
    assert check_rule(rule) == [
        "no match case (tests.match is empty)",
        "no no_match case (tests.no_match is empty)",
    ]


def test_wrong_expectations_are_reported_with_index() -> None:
    rule = _rule(
        "SELECT * FROM events WHERE actor_type = 'Root'",
        RuleTests(match=[{"actor_type": "IAMUser"}], no_match=[{"actor_type": "Root"}]),
    )
    assert check_rule(rule) == [
        "match[0]: expected a detection, got none",
        "no_match[0]: expected no detection, got 1",
    ]


def test_multi_event_case_runs_in_isolation() -> None:
    key = {"actor_id": "u1", "action": "CreateAccessKey", "event_time": "2026-01-01 10:00:00"}
    soon = {"actor_id": "u1", "action": "AttachUserPolicy", "event_time": "2026-01-01 10:30:00"}
    late = {"actor_id": "u1", "action": "AttachUserPolicy", "event_time": "2026-01-01 12:00:00"}
    rule = _rule(PAIR_QUERY, RuleTests(match=[[key, soon]], no_match=[[key, late], soon]))
    assert check_rule(rule) == []


def test_bad_fixture_value_is_a_case_failure_not_a_crash() -> None:
    rule = _rule(
        "SELECT * FROM events",
        RuleTests(
            match=[{"mfa_used": "maybe"}, {"actor_type": "Root"}], no_match=[{"mfa_used": "maybe"}]
        ),
    )
    failures = check_rule(rule)
    assert len(failures) == 2
    assert failures[0].startswith("match[0]: error running case: ")
    assert failures[1].startswith("no_match[0]: error running case: ")


def test_query_runtime_error_is_a_case_failure_and_later_cases_still_run() -> None:
    rule = _rule(
        "SELECT event_id FROM events WHERE CAST(action AS INTEGER) = 1",
        RuleTests(match=[{"action": "abc"}, {"action": "1"}], no_match=[{"action": "2"}]),
    )
    failures = check_rule(rule)
    assert len(failures) == 1
    assert failures[0].startswith("match[0]: error running case: ")


def test_unknown_fixture_column_names_rule_and_case() -> None:
    rule = _rule("SELECT * FROM events", RuleTests(match=[{"acton": "x"}], no_match=[{}]))
    with pytest.raises(FatalError, match=r"rule 'r' match\[0\]: fixture event has unknown columns"):
        check_rule(rule)
