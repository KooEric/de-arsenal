"""The only definition of grade thresholds. Change them here and nowhere else."""

from __future__ import annotations

from typing import Final

GRADE_THRESHOLDS: Final[tuple[tuple[str, float], ...]] = (
    ("A+", 95.0),
    ("A", 90.0),
    ("B", 80.0),
    ("C", 70.0),
    ("D", 60.0),
)
LOWEST_GRADE: Final = "F"
BLOCKER_CAP: Final = "C"
_ORDER: Final = tuple(grade for grade, _ in GRADE_THRESHOLDS) + (LOWEST_GRADE,)


def score_to_grade(score: float) -> str:
    for grade, floor in GRADE_THRESHOLDS:
        if score >= floor:
            return grade
    return LOWEST_GRADE


def cap_grade(grade: str, cap: str) -> str:
    """Return the worse of `grade` and `cap`."""
    return grade if _ORDER.index(grade) >= _ORDER.index(cap) else cap
