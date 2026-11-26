from datetime import UTC, datetime, timedelta
from pathlib import Path

from _projects import bare_project
from _state import fail_dataset, publish_dataset, snapshot, write_state

from censor.checks.freshness import check_freshness
from censor.checks.reliability import check_reliability
from censor.context import load_context
from censor.model import CheckResult, Status
from censor.target import AuditContext

NOW = datetime(2026, 11, 25, 9, 30, 0, tzinfo=UTC)
RAN = NOW - timedelta(hours=2)


def _ctx(root: Path, now: datetime = NOW, max_age_hours: float | None = None) -> AuditContext:
    return AuditContext(
        kind="project",
        root=root,
        now=now,
        project=load_context(root),
        max_age_hours=max_age_hours,
    )


def _fresh(root: Path, **kw: object) -> dict[str, CheckResult]:
    return {c.id: c for c in check_freshness(_ctx(root, **kw))}  # type: ignore[arg-type]


def test_never_run_pipeline_is_na(tmp_path: Path) -> None:
    checks = _fresh(bare_project(tmp_path))
    assert checks["freshness.last_success:collect.yaml"].status == Status.NA
    assert "freshness.dataset_state" not in checks  # no `arsenal dataset` store in this project


def test_last_success_passes_and_reports_the_age(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    write_state(root, at=RAN, done=2)
    check = _fresh(root)["freshness.last_success:collect.yaml"]
    assert check.status == Status.PASS
    assert "2026-11-25 07:30" in check.evidence and "2.0h ago" in check.evidence


def test_last_success_older_than_the_limit_fails(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    write_state(root, at=RAN, done=2)
    assert (
        _fresh(root, max_age_hours=3)["freshness.last_success:collect.yaml"].status == Status.PASS
    )
    stale = _fresh(root, max_age_hours=1)["freshness.last_success:collect.yaml"]
    assert stale.status == Status.FAIL and "limit 1h" in stale.evidence


def test_a_pipeline_whose_units_all_failed_has_no_success(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    write_state(root, at=RAN, failed=2)
    assert _fresh(root)["freshness.last_success:collect.yaml"].status == Status.FAIL


def test_no_freshness_limit_anywhere_is_a_warning(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    write_state(root, at=RAN, done=1)
    assert _fresh(root)["freshness.max_age_set"].status == Status.WARN
    assert _fresh(root, max_age_hours=24)["freshness.max_age_set"].status == Status.PASS


def test_published_dataset_with_a_limit_passes_then_goes_stale(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    publish_dataset(root, max_age_hours=24)
    published_at = datetime.now(UTC)
    before = snapshot(root)
    checks = _fresh(root, now=published_at + timedelta(hours=1))
    assert checks["freshness.dataset_state"].status == Status.PASS
    assert checks["freshness.max_age_set"].status == Status.PASS
    late = _fresh(root, now=published_at + timedelta(hours=30))["freshness.dataset_state"]
    assert late.status == Status.FAIL and "stale" in late.evidence
    assert snapshot(root) == before


def test_failed_dataset_fails_freshness_and_keeps_reliability_when_published_remains(
    tmp_path: Path,
) -> None:
    root = bare_project(tmp_path)
    store = publish_dataset(root)
    fail_dataset(store)
    assert _fresh(root)["freshness.dataset_state"].status == Status.FAIL
    kept = {c.id: c for c in check_reliability(_ctx(root))}["reliability.dataset_publication"]
    assert kept.status == Status.PASS
    fail_dataset(store, drop_published=True)
    gone = {c.id: c for c in check_reliability(_ctx(root))}["reliability.dataset_publication"]
    assert gone.status == Status.FAIL


def test_healthy_dataset_has_no_publication_finding(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    publish_dataset(root)
    checks = {c.id: c for c in check_reliability(_ctx(root))}
    assert checks["reliability.dataset_publication"].status == Status.NA
