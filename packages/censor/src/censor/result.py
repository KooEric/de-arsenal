"""The one place a `CheckResult` is built, so every check drops its fix once it passes."""

from __future__ import annotations

from censor.model import CheckResult, Dimension, Status


def make_result(
    check_id: str,
    dimension: Dimension,
    weight: int,
    status: Status,
    subject: str,
    evidence: str,
    fix: str = "",
    *,
    blocker: bool = False,
) -> CheckResult:
    return CheckResult(
        id=check_id,
        dimension=dimension,
        weight=weight,
        status=status,
        blocker=blocker,
        subject=subject,
        evidence=evidence,
        fix=fix if status != Status.PASS else "",
    )
