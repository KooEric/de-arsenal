from pathlib import Path

from _projects import bare_project

from censor.context import ProjectContext, load_context
from censor.model import CheckResult, Dimension, Status
from censor.runner import CheckGroup, run_checks


def _ok(ctx: ProjectContext) -> list[CheckResult]:
    return [
        CheckResult(
            id="ok",
            dimension=Dimension.GUARDS,
            weight=1,
            status=Status.PASS,
            subject=str(ctx.root),
            evidence="fine",
        )
    ]


def _boom(ctx: ProjectContext) -> list[CheckResult]:
    raise RuntimeError("exploded")


def test_a_crashing_group_becomes_one_error_check_and_others_still_run(tmp_path: Path) -> None:
    ctx = load_context(bare_project(tmp_path))
    results = run_checks(
        ctx,
        [CheckGroup("boom", Dimension.SECURITY, _boom), CheckGroup("ok", Dimension.GUARDS, _ok)],
    )
    assert [r.id for r in results] == ["boom.crashed", "ok"]
    crashed = results[0]
    assert crashed.status == Status.ERROR
    assert crashed.dimension == Dimension.SECURITY
    assert "RuntimeError: exploded" in crashed.evidence
