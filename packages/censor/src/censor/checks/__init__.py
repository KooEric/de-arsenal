"""The Quick tier: checks that read files only, never data or the network."""

from censor.checks.agent import check_agent_readiness
from censor.checks.correctness import check_correctness
from censor.checks.guards import check_guards
from censor.checks.operability import check_operability
from censor.checks.security import check_security
from censor.model import Dimension
from censor.runner import CheckGroup

QUICK_CHECKS: tuple[CheckGroup, ...] = (
    CheckGroup("correctness", Dimension.CORRECTNESS, check_correctness),
    CheckGroup("guards", Dimension.GUARDS, check_guards),
    CheckGroup("security", Dimension.SECURITY, check_security),
    CheckGroup("operability", Dimension.OPERABILITY, check_operability),
    CheckGroup("agent", Dimension.AGENT_READINESS, check_agent_readiness),
)
