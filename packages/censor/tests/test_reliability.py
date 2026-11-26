from datetime import UTC, datetime, timedelta
from pathlib import Path

from _projects import bare_project
from _state import write_split_dlq, write_state

from censor.checks.reliability import DLQ_FAIL_RATIO, check_reliability
from censor.context import load_context
from censor.model import CheckResult, Status
from censor.target import AuditContext

NOW = datetime(2026, 11, 25, 9, 30, 0, tzinfo=UTC)
RAN = NOW - timedelta(hours=2)


def _run(root: Path) -> dict[str, CheckResult]:
    ctx = AuditContext(kind="project", root=root, now=NOW, project=load_context(root))
    return {c.id: c for c in check_reliability(ctx)}


def test_never_run_pipeline_is_na_not_fail(tmp_path: Path) -> None:
    checks = _run(bare_project(tmp_path))
    assert checks and all(c.status == Status.NA for c in checks.values())
    assert "never run" in checks["reliability.failed_units:collect.yaml"].evidence


def test_clean_run_passes(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    write_state(root, at=RAN, done=5)
    checks = _run(root)
    assert checks["reliability.failed_units:collect.yaml"].status == Status.PASS
    assert checks["reliability.rejected_rows:collect.yaml"].status == Status.PASS
    assert checks["reliability.output_kept:collect.yaml"].status == Status.NA
    assert "reliability.active_run:collect.yaml" not in checks


def test_failed_and_quarantined_units_fail(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    write_state(root, at=RAN, done=5, failed=2, quarantined=1)
    check = _run(root)["reliability.failed_units:collect.yaml"]
    assert check.status == Status.FAIL
    assert "2 failed" in check.evidence and "1 quarantined" in check.evidence
    assert "arsenal" in check.fix


def test_rejected_rows_warn_below_the_limit_and_fail_above_it(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    write_state(root, at=RAN, done=10, rows_per_unit=100)  # 1000 rows done
    write_split_dlq(root, 10)
    assert _run(root)["reliability.rejected_rows:collect.yaml"].status == Status.WARN
    over = int(1000 * DLQ_FAIL_RATIO / (1 - DLQ_FAIL_RATIO)) + 20
    write_split_dlq(root, over)
    check = _run(root)["reliability.rejected_rows:collect.yaml"]
    assert check.status == Status.FAIL and f"{over} rejected" in check.evidence


def test_output_kept_after_a_failure(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    write_state(root, at=RAN, done=1, failed=1)
    assert _run(root)["reliability.output_kept:collect.yaml"].status == Status.FAIL
    out = root / "data" / "raw"
    out.mkdir(parents=True)
    (out / "part-0.parquet").write_bytes(b"x")
    assert _run(root)["reliability.output_kept:collect.yaml"].status == Status.PASS


def test_a_held_lease_is_a_warning(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    write_state(root, at=NOW, done=1, lease_seconds=600)
    check = _run(root)["reliability.active_run:collect.yaml"]
    assert check.status == Status.WARN and "running" in check.evidence


def test_unreadable_state_is_a_warning_and_the_rest_is_na(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    db = root / ".arsenal" / "demo-collect.db"
    db.parent.mkdir()
    db.write_bytes(b"garbage that is not sqlite at all, repeated " * 8)
    checks = _run(root)
    assert checks["reliability.active_run:collect.yaml"].status == Status.WARN
    assert checks["reliability.failed_units:collect.yaml"].status == Status.NA


def test_unreadable_dlq_record_is_an_error_for_that_pipeline_only(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    write_state(root, at=RAN, done=1)
    folder = root / ".arsenal" / "dlq" / "demo-collect"
    folder.mkdir(parents=True)
    (folder / "u.json").write_text("{not json")
    checks = _run(root)
    assert checks["reliability.state:collect.yaml"].status == Status.ERROR
