from datetime import UTC, datetime
from pathlib import Path

from _projects import production_project

from censor.audit import AuditRequest, run_audit
from censor.checks import QUICK_CHECKS, STANDARD_CHECKS
from censor.model import CheckResult, Dimension, Status
from censor.result import make_result
from censor.runner import CheckGroup, run_checks
from censor.scoring import score_checks
from censor.target import AuditContext, FilesTarget

NOW = datetime(2026, 11, 25, 9, 30, 0, tzinfo=UTC)


def _files_ctx(root: Path) -> AuditContext:
    return AuditContext(kind="files", root=root, now=NOW, data=FilesTarget("data/*.parquet"))


def _one(dimension: Dimension) -> list[CheckResult]:
    return [make_result("x", dimension, 1, Status.PASS, "s", "fine")]


def test_a_group_that_does_not_apply_to_the_target_kind_is_not_run(tmp_path: Path) -> None:
    calls: list[str] = []

    def project_only(ctx: AuditContext) -> list[CheckResult]:
        calls.append("project")
        return _one(Dimension.GUARDS)

    def any_kind(ctx: AuditContext) -> list[CheckResult]:
        calls.append("any")
        return _one(Dimension.DATA_HEALTH)

    results = run_checks(
        _files_ctx(tmp_path),
        [
            CheckGroup("p", Dimension.GUARDS, project_only, kinds=frozenset({"project"})),
            CheckGroup("a", Dimension.DATA_HEALTH, any_kind),
        ],
    )
    assert calls == ["any"]
    scores = {d.dimension: d.score for d in score_checks(results).dimensions}
    assert scores[Dimension.GUARDS] is None
    assert scores[Dimension.DATA_HEALTH] == 10.0


def test_quick_groups_yield_nothing_for_a_data_target(tmp_path: Path) -> None:
    assert run_checks(_files_ctx(tmp_path), QUICK_CHECKS) == []


def test_standard_checks_start_with_the_quick_ones() -> None:
    assert STANDARD_CHECKS[: len(QUICK_CHECKS)] == QUICK_CHECKS
    standard_only = {g.dimension for g in STANDARD_CHECKS[len(QUICK_CHECKS) :]}
    assert standard_only == {Dimension.RELIABILITY, Dimension.FRESHNESS, Dimension.DATA_HEALTH}


def test_make_result_drops_the_fix_on_pass_and_keeps_it_otherwise() -> None:
    passed = make_result("a", Dimension.GUARDS, 2, Status.PASS, "s", "ok", "do this")
    failed = make_result("a", Dimension.GUARDS, 2, Status.FAIL, "s", "bad", "do this", blocker=True)
    assert passed.fix == "" and failed.fix == "do this"
    assert failed.blocker and not passed.blocker


def test_run_audit_fills_tier_and_target_kind(tmp_path: Path) -> None:
    root = production_project(tmp_path)
    quick = run_audit(AuditRequest(root=root, tier="quick", now=NOW))
    assert (quick.report.tier, quick.report.target_kind) == ("quick", "project")
    assert quick.report.run_id == "20261125-093000-quick"
    assert quick.tool_errors == ()
    standard = run_audit(AuditRequest(root=root, tier="standard", now=NOW))
    assert standard.report.tier == "standard"
    assert standard.report.run_id == "20261125-093000-standard"
    quick_ids = [c.id for c in quick.report.checks]
    assert [c.id for c in standard.report.checks][: len(quick_ids)] == quick_ids
