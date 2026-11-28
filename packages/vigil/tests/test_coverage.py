import json
from datetime import datetime
from pathlib import Path

from _events import write_events

from vigil.coverage import CoverageReport, build_coverage, load_techniques
from vigil.events import load_events
from vigil.rule import Rule, RuleTests

SCHEMA = Path(__file__).parents[3] / "schemas" / "vigil-coverage.json"

ROOT = Rule(
    id="root-login", title="t", severity="high", attack=["T1078.004"], source="cloudtrail",
    query="SELECT * FROM events WHERE actor_type = 'Root' AND mfa_used IS NULL",
    tests=RuleTests(match=[{"actor_type": "Root"}], no_match=[{"actor_type": "IAMUser"}]),
)  # fmt: skip
ODD = Rule(
    id="odd", title="t", severity="low", attack=["T9999"], source="cloudtrail",
    query="SELECT * FROM events WHERE action = 'X'",
)  # fmt: skip


def test_technique_list_is_valid_and_unique() -> None:
    techniques = load_techniques()
    assert len(techniques) == 20
    assert len({t.id for t in techniques}) == 20


def test_coverage_from_rules_only() -> None:
    report = build_coverage([ROOT, ODD], None)
    assert report.rules == 2
    by_id = {t.id: t for t in report.techniques}
    assert by_id["T1078.004"].rules == ["root-login"]
    assert by_id["T1078.004"].tests == 2
    assert "T1530" in report.uncovered
    assert "T1078.004" not in report.uncovered
    assert report.unlisted == ["T9999"]
    assert [(s.source, s.rules, s.last_event_time) for s in report.sources] == [
        ("cloudtrail", 2, None)
    ]
    columns = {c.column: c for c in report.columns}
    assert columns["actor_type"].used_by == ["root-login"]
    assert columns["action"].used_by == ["odd"]
    assert columns["mfa_used"].null_ratio is None
    assert "region" not in columns


def test_coverage_with_events_adds_null_ratio_and_last_event(tmp_path: Path) -> None:
    write_events(
        tmp_path / "events" / "a.parquet",
        [
            {"event_id": "e1", "source": "cloudtrail", "actor_type": "Root",
             "event_time": datetime(2026, 11, 1, 10)},
            {"event_id": "e2", "source": "cloudtrail", "actor_type": None, "mfa_used": True,
             "event_time": datetime(2026, 11, 2, 10)},
        ],
    )  # fmt: skip
    report = build_coverage([ROOT], load_events(str(tmp_path / "events")))
    columns = {c.column: c for c in report.columns}
    assert columns["actor_type"].null_ratio == 0.5
    assert columns["mfa_used"].null_ratio == 0.5
    assert report.sources[0].last_event_time == datetime(2026, 11, 2, 10)


def test_schema_file_matches_model() -> None:
    assert json.loads(SCHEMA.read_text(encoding="utf-8")) == CoverageReport.model_json_schema()
