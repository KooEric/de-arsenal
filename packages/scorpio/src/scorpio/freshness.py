"""Deterministic freshness evaluation for state-store timestamps."""

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Freshness:
    status: str  # fresh | stale | unknown
    age_seconds: float | None
    max_age_seconds: float
    last_completed_at: str | None
    message: str

    @property
    def alert(self) -> bool:
        return self.status == "stale"


def assess_freshness(
    last_completed_at: str | None,
    *,
    max_age_seconds: float,
    now: datetime | None = None,
) -> Freshness:
    """Classify the last completed run as fresh, stale, or unknown."""
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    if last_completed_at is None:
        return Freshness(
            status="unknown",
            age_seconds=None,
            max_age_seconds=max_age_seconds,
            last_completed_at=None,
            message="no completed unit has been recorded",
        )
    try:
        completed = datetime.fromisoformat(last_completed_at)
    except ValueError as e:
        raise ValueError(f"invalid completion timestamp: {last_completed_at!r}") from e
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=UTC)
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    age = max(0.0, (reference - completed).total_seconds())
    status = "fresh" if age <= max_age_seconds else "stale"
    message = f"last completion is {age:.1f}s old (limit {max_age_seconds:.1f}s)"
    return Freshness(status, age, max_age_seconds, last_completed_at, message)
