"""Load -> check -> score. The one entry point the CLI calls."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from censor import __version__
from censor.checks import QUICK_CHECKS
from censor.context import load_context
from censor.model import AuditReport
from censor.runner import run_checks
from censor.scoring import score_checks


def new_run_id(tier: str, now: datetime | None = None) -> str:
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    return f"{moment:%Y%m%d-%H%M%S}-{tier}"


def audit_project(root: Path, *, now: datetime | None = None) -> AuditReport:
    ctx = load_context(root)
    checks = run_checks(ctx, QUICK_CHECKS)
    scored = score_checks(checks)
    return AuditReport(
        run_id=new_run_id("quick", now),
        tier="quick",
        target=str(ctx.root),
        target_kind="project",
        censor_version=__version__,
        score=scored.score,
        grade=scored.grade,
        capped_by_blocker=scored.capped_by_blocker,
        dimensions=scored.dimensions,
        checks=checks,
    )
