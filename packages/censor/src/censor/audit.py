"""Load -> check -> score. The one entry point the CLI calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from censor import __version__
from censor.checks import QUICK_CHECKS, STANDARD_CHECKS
from censor.context import load_context
from censor.model import AuditReport
from censor.runner import run_checks
from censor.scoring import score_checks
from censor.target import AuditContext, DataTarget, ScanOptions, Tier, kind_of


def new_run_id(tier: str, now: datetime | None = None) -> str:
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    return f"{moment:%Y%m%d-%H%M%S}-{tier}"


@dataclass(frozen=True)
class AuditRequest:
    root: Path  # project directory, or where a data target's history goes
    tier: Tier = "quick"
    now: datetime | None = None
    data: DataTarget | None = None
    key: tuple[str, ...] = ()
    time_column: str | None = None
    max_age_hours: float | None = None
    scan: ScanOptions = field(default_factory=ScanOptions)


@dataclass(frozen=True)
class AuditOutcome:
    report: AuditReport
    tool_errors: tuple[str, ...] = ()  # the target could not be read: exit code 2


def _context(request: AuditRequest, now: datetime) -> AuditContext:
    root = request.root.absolute()
    if request.data is None:
        return AuditContext(kind="project", root=root, now=now, project=load_context(root))
    return AuditContext(
        kind=kind_of(request.data),
        root=root,
        now=now,
        data=request.data,
        key=request.key,
        time_column=request.time_column,
        max_age_hours=request.max_age_hours,
        scan=request.scan,
    )


def run_audit(request: AuditRequest) -> AuditOutcome:
    now = (request.now or datetime.now(UTC)).astimezone(UTC)
    ctx = _context(request, now)
    groups = QUICK_CHECKS if request.tier == "quick" else STANDARD_CHECKS
    checks = run_checks(ctx, groups)
    scored = score_checks(checks)
    report = AuditReport(
        run_id=new_run_id(request.tier, now),
        tier=request.tier,
        target=str(ctx.root) if ctx.data is None else ctx.data.label,
        target_kind=ctx.kind,
        censor_version=__version__,
        score=scored.score,
        grade=scored.grade,
        capped_by_blocker=scored.capped_by_blocker,
        dimensions=scored.dimensions,
        checks=checks,
    )
    return AuditOutcome(report)


def audit_project(root: Path, *, now: datetime | None = None) -> AuditReport:
    """Quick audit of a project (reads files only)."""
    return run_audit(AuditRequest(root=root, tier="quick", now=now)).report
