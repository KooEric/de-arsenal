"""Quick tier: a stopped pipeline is caught from file times alone, without opening any data."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from _projects import bare_project, write_project
from _state import snapshot

from censor.audit import AuditRequest, run_audit
from censor.checks import QUICK_CHECKS, STANDARD_CHECKS
from censor.checks.staleness import check_staleness
from censor.context import load_context
from censor.model import CheckResult, Status
from censor.target import AuditContext

NOW = datetime(2026, 11, 26, 9, 0, 0, tzinfo=UTC)
CHECK = "operability.freshness:collect.yaml"
POSTGRES_COLLECT = """\
name: demo-collect
source:
  type: file
  path: ./input/*.csv
  format: csv
sink:
  type: postgres
  dsn: ${PG_DSN}
  table: rows
"""


def _touch(path: Path, at: datetime) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    os.utime(path, (at.timestamp(), at.timestamp()))
    return path


def _check(root: Path, max_age_hours: float | None = None) -> CheckResult:
    ctx = AuditContext(
        kind="project",
        root=root,
        now=NOW,
        project=load_context(root),
        max_age_hours=max_age_hours,
    )
    return {c.id: c for c in check_staleness(ctx)}[CHECK]


def test_a_pipeline_with_no_output_and_no_state_is_na(tmp_path: Path) -> None:
    assert _check(bare_project(tmp_path)).status == Status.NA


def test_output_written_recently_passes(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    _touch(root / "data" / "raw" / "part-0.parquet", NOW - timedelta(hours=5))
    check = _check(root)
    assert check.status == Status.PASS
    assert "2026-11-26 04:00" in check.evidence and "limit 168h" in check.evidence


def test_output_untouched_for_months_warns_without_any_limit_given(tmp_path: Path) -> None:
    # The picca-lake case: collection stopped on 8/13 and nothing said so.
    root = bare_project(tmp_path)
    stopped_on = datetime(2026, 8, 13, tzinfo=UTC)
    _touch(root / "data" / "raw" / "2026-08-13" / "part-0.parquet", stopped_on)
    check = _check(root)
    assert check.status == Status.WARN
    assert "2026-08-13" in check.evidence and "default limit 168h" in check.evidence
    assert check.fix


def test_the_newest_file_anywhere_under_the_output_counts(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    _touch(root / "data" / "raw" / "old" / "a.parquet", NOW - timedelta(days=90))
    _touch(root / "data" / "raw" / "new" / "b.parquet", NOW - timedelta(hours=1))
    assert _check(root).status == Status.PASS


def test_a_given_limit_replaces_the_default(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    _touch(root / "data" / "raw" / "a.parquet", NOW - timedelta(days=2))
    assert _check(root).status == Status.PASS
    tight = _check(root, max_age_hours=24)
    assert tight.status == Status.WARN and "limit 24h" in tight.evidence


def test_a_remote_sink_falls_back_to_the_state_file(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    write_project(root, {"collect.yaml": POSTGRES_COLLECT})
    _touch(root / ".arsenal" / "demo-collect.db", NOW - timedelta(days=30))
    check = _check(root)
    assert check.status == Status.WARN and "state" in check.evidence


def test_reading_file_times_changes_nothing(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    _touch(root / "data" / "raw" / "a.parquet", NOW - timedelta(days=30))
    before = snapshot(root)
    _check(root)
    assert snapshot(root) == before


def test_both_tiers_run_it() -> None:
    assert "staleness" in {g.name for g in QUICK_CHECKS}
    assert "staleness" in {g.name for g in STANDARD_CHECKS}


def test_a_stopped_lake_scores_below_a_running_one(tmp_path: Path) -> None:
    stopped = bare_project(tmp_path / "stopped")
    running = bare_project(tmp_path / "running")
    _touch(stopped / "data" / "raw" / "a.parquet", datetime(2026, 8, 13, tzinfo=UTC))
    _touch(running / "data" / "raw" / "a.parquet", NOW - timedelta(hours=3))

    def score(root: Path) -> float:
        report = run_audit(AuditRequest(root=root, tier="quick", now=NOW)).report
        assert report.score is not None
        return report.score

    assert score(stopped) < score(running)
