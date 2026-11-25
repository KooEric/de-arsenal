"""Frozen result models. `AuditReport` is the stdout JSON contract (schemas/censor-report.json)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Status(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NA = "na"
    ERROR = "error"


class Dimension(StrEnum):
    CORRECTNESS = "correctness"
    GUARDS = "guards"
    SECURITY = "security"
    OPERABILITY = "operability"
    AGENT_READINESS = "agent_readiness"
    RELIABILITY = "reliability"
    FRESHNESS = "freshness"
    DATA_HEALTH = "data_health"


DIMENSION_WEIGHTS: dict[Dimension, int] = {
    Dimension.CORRECTNESS: 20,
    Dimension.GUARDS: 15,
    Dimension.SECURITY: 15,
    Dimension.OPERABILITY: 5,
    Dimension.AGENT_READINESS: 5,
    Dimension.RELIABILITY: 15,
    Dimension.FRESHNESS: 10,
    Dimension.DATA_HEALTH: 15,
}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CheckResult(_Frozen):
    id: str = Field(description="Stable check id, e.g. `guards.validate:collect.yaml`")
    dimension: Dimension
    weight: int = Field(ge=1)
    status: Status
    blocker: bool = Field(default=False, description="A failed blocker caps the grade at C")
    subject: str = Field(description="File, table or directory the check looked at")
    evidence: str = Field(description="What was found; never contains a secret value")
    fix: str = Field(default="", description="One line on how to fix it")


class DimensionScore(_Frozen):
    dimension: Dimension
    weight: int
    score: float | None = Field(description="0-10, or null when no check applied")


class AuditReport(_Frozen):
    schema_version: Literal[1] = 1
    run_id: str
    tier: Literal["quick", "standard"] = Field(
        description="quick reads files only; standard (Plan 2) also inspects state and data"
    )
    target: str
    target_kind: Literal["project", "files", "duckdb", "postgres"] = Field(
        description="What `target` names; Plan 1 audits a DE Arsenal `project` only"
    )
    censor_version: str
    score: float | None = Field(description="0-100, or null when nothing applied")
    grade: str | None
    capped_by_blocker: bool
    dimensions: list[DimensionScore]
    checks: list[CheckResult]

    @property
    def has_failures(self) -> bool:
        return any(c.status in (Status.FAIL, Status.ERROR) for c in self.checks)
