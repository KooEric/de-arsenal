"""Check groups by tier. Quick reads files only; Standard also reads state and data."""

from collections.abc import Callable

from censor.checks.agent import check_agent_readiness
from censor.checks.correctness import check_correctness
from censor.checks.data_health import check_data_health
from censor.checks.freshness import check_freshness
from censor.checks.guards import check_guards
from censor.checks.operability import check_operability
from censor.checks.reliability import check_reliability
from censor.checks.security import check_security
from censor.checks.staleness import check_staleness
from censor.context import ProjectContext
from censor.model import CheckResult, Dimension
from censor.runner import CheckGroup
from censor.target import PROJECT_ONLY, AuditContext

Group = CheckGroup[AuditContext]


def _project(
    name: str, dimension: Dimension, fn: Callable[[ProjectContext], list[CheckResult]]
) -> Group:
    """A Quick group: reads the project's files, so it applies to project targets only."""

    def run(ctx: AuditContext) -> list[CheckResult]:
        if ctx.project is None:
            raise RuntimeError(f"{name} needs a project target")
        return fn(ctx.project)

    return CheckGroup(name, dimension, run, kinds=PROJECT_ONLY)


QUICK_CHECKS: tuple[Group, ...] = (
    _project("correctness", Dimension.CORRECTNESS, check_correctness),
    _project("guards", Dimension.GUARDS, check_guards),
    _project("security", Dimension.SECURITY, check_security),
    _project("operability", Dimension.OPERABILITY, check_operability),
    _project("agent", Dimension.AGENT_READINESS, check_agent_readiness),
    # File times only: a stopped pipeline must not grade like a running one.
    CheckGroup("staleness", Dimension.OPERABILITY, check_staleness, kinds=PROJECT_ONLY),
)

STANDARD_CHECKS: tuple[Group, ...] = (
    *QUICK_CHECKS,
    CheckGroup("reliability", Dimension.RELIABILITY, check_reliability, kinds=PROJECT_ONLY),
    CheckGroup("freshness", Dimension.FRESHNESS, check_freshness),
    CheckGroup("data_health", Dimension.DATA_HEALTH, check_data_health),
)
