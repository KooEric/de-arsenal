"""Standard tier: freshness (filled in by the next task)."""

from __future__ import annotations

from censor.model import CheckResult
from censor.target import AuditContext


def check_freshness(ctx: AuditContext) -> list[CheckResult]:
    return []
