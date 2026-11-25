"""Checks -> dimension scores -> overall score and grade. Deterministic."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from censor.grade import BLOCKER_CAP, cap_grade, score_to_grade
from censor.model import DIMENSION_WEIGHTS, CheckResult, Dimension, DimensionScore, Status

WARN_CREDIT: Final = 0.5
_SCORED: Final = (Status.PASS, Status.WARN, Status.FAIL)


@dataclass(frozen=True)
class Scored:
    dimensions: list[DimensionScore]
    score: float | None
    grade: str | None
    capped_by_blocker: bool


def dimension_score(checks: Sequence[CheckResult], dimension: Dimension) -> float | None:
    applicable = [c for c in checks if c.dimension == dimension and c.status in _SCORED]
    total = sum(c.weight for c in applicable)
    if total == 0:
        return None
    earned = sum(
        c.weight * (1.0 if c.status == Status.PASS else WARN_CREDIT)
        for c in applicable
        if c.status != Status.FAIL
    )
    return 10.0 * earned / total


def score_checks(checks: Sequence[CheckResult]) -> Scored:
    dimensions = [
        DimensionScore(dimension=d, weight=w, score=dimension_score(checks, d))
        for d, w in DIMENSION_WEIGHTS.items()
    ]
    applied = [d for d in dimensions if d.score is not None]
    weight = sum(d.weight for d in applied)
    if weight == 0:
        return Scored(dimensions, None, None, False)
    score = 10.0 * sum(d.weight * (d.score or 0.0) for d in applied) / weight
    grade = score_to_grade(score)
    blocked = any(c.blocker and c.status == Status.FAIL for c in checks)
    capped = cap_grade(grade, BLOCKER_CAP) if blocked else grade
    return Scored(dimensions, score, capped, blocked and capped != grade)
