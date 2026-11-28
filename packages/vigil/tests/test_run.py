from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from _events import write_events

from vigil.events import load_events
from vigil.rule import Rule
from vigil.run import ALERT_SCHEMA, alert_id, existing_alert_ids, run_rules

NOW = datetime(2026, 11, 28, 9, 0, 0)

ROOT = Rule(
    id="root-login", title="t", severity="high", attack=["T1078.004"], source="cloudtrail",
    query="SELECT * FROM events WHERE actor_type = 'Root'",
)  # fmt: skip
BROKEN = Rule(
    id="broken", title="t", severity="low", attack=["T1530"], source="cloudtrail",
    query="SELECT event_id FROM events WHERE CAST(action AS INTEGER) = 1",
)  # fmt: skip


def _lake(tmp_path: Path) -> str:
    write_events(
        tmp_path / "events" / "a.parquet",
        [
            {"event_id": "e1", "actor_type": "Root", "actor_id": "root", "action": "ConsoleLogin",
             "event_time": datetime(2026, 11, 1, 10)},
            {"event_id": "e2", "actor_type": "IAMUser", "actor_id": "u1", "action": "ConsoleLogin",
             "event_time": datetime(2026, 11, 1, 11)},
        ],
    )  # fmt: skip
    return str(tmp_path / "events")


def test_alert_id_is_stable_and_rule_specific() -> None:
    assert alert_id("r1", "e1") == alert_id("r1", "e1")
    assert alert_id("r1", "e1") != alert_id("r2", "e1")
    assert len(alert_id("r1", "e1")) == 32


def test_run_writes_alerts_with_the_alert_schema(tmp_path: Path) -> None:
    out = tmp_path / "alerts"
    summary = run_rules([ROOT], load_events(_lake(tmp_path)), out, NOW)
    assert summary.new_by_rule == {"root-login": 1}
    assert summary.new_severities == frozenset({"high"})
    (file,) = list(out.glob("*.parquet"))
    table = pq.read_table(file)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    assert table.schema.equals(ALERT_SCHEMA)  # pyright: ignore[reportUnknownMemberType]
    row = table.to_pylist()[0]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    assert (row["rule_id"], row["event_id"], row["actor_id"]) == ("root-login", "e1", "root")
    assert row["attack"] == ["T1078.004"]
    assert row["detected_at"] == NOW


def test_second_run_adds_nothing(tmp_path: Path) -> None:
    out = tmp_path / "alerts"
    lake = _lake(tmp_path)
    run_rules([ROOT], load_events(lake), out, NOW)
    summary = run_rules([ROOT], load_events(lake), out, NOW)
    assert summary.new_by_rule == {"root-login": 0}
    assert summary.new_severities == frozenset()
    assert len(list(out.glob("*.parquet"))) == 1
    assert len(existing_alert_ids(out)) == 1


def test_a_failing_rule_does_not_stop_the_others(tmp_path: Path) -> None:
    out = tmp_path / "alerts"
    summary = run_rules([BROKEN, ROOT], load_events(_lake(tmp_path)), out, NOW)
    assert list(summary.failed) == ["broken"]
    assert summary.new_by_rule == {"root-login": 1}


def test_no_detections_writes_no_file(tmp_path: Path) -> None:
    out = tmp_path / "alerts"
    quiet = ROOT.model_copy(update={"query": "SELECT * FROM events WHERE actor_type = 'Nobody'"})
    run_rules([quiet], load_events(_lake(tmp_path)), out, NOW)
    assert not out.exists() or not list(out.iterdir())


def test_rule_query_ending_in_line_comment_still_runs(tmp_path: Path) -> None:
    commented = Rule(
        id="commented", title="t", severity="high", attack=["T1078.004"], source="cloudtrail",
        query="SELECT * FROM events WHERE actor_type = 'Root' -- note",
    )  # fmt: skip
    summary = run_rules([commented], load_events(_lake(tmp_path)), tmp_path / "alerts", NOW)
    assert summary.failed == {}
    assert summary.new_by_rule == {"commented": 1}


def test_failed_write_leaves_no_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_table: object, where: Path) -> None:
        where.write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr("vigil.run.pq.write_table", boom)
    out = tmp_path / "alerts"
    with pytest.raises(OSError, match="disk full"):
        run_rules([ROOT], load_events(_lake(tmp_path)), out, NOW)
    assert list(out.glob("*")) == [] and list(out.glob(".*")) == []
