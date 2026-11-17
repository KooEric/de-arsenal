"""Health rules for Hasta streams and Ballista runs.

Pure functions over plain observations, so Scorpio stays dependency-free and
any tool (``hasta checkpoint show``, ``arsenal status``) can feed it. Every
rule that fires adds a short reason; ``alert`` is true for anything a person
should look at.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

# Ordered from worst to best: the first matching rule decides the status.
STREAM_STATUSES = ("failing", "stalled", "lagging", "unknown", "healthy")
_TERMINAL_OK = "succeeded"
_TERMINAL_BAD = frozenset({"failed", "lost"})


@dataclass(frozen=True)
class StreamObservation:
    last_commit_at: str | None
    lease_expires_at: str | None = None
    failed_batches: int = 0
    pending_batches: int = 0
    dlq_batches: int = 0
    late_rows: int = 0


@dataclass(frozen=True)
class StreamHealth:
    status: str
    lag_seconds: float | None
    max_lag_seconds: float
    reasons: tuple[str, ...]

    @property
    def alert(self) -> bool:
        return self.status in {"failing", "stalled", "lagging"}


def assess_stream(
    observation: StreamObservation,
    *,
    max_lag_seconds: float,
    now: datetime | None = None,
) -> StreamHealth:
    if max_lag_seconds <= 0:
        raise ValueError("max_lag_seconds must be positive")
    reference = _aware(now or datetime.now(UTC))
    lag = None
    if observation.last_commit_at is not None:
        lag = max(0.0, (reference - _parse(observation.last_commit_at)).total_seconds())

    reasons: list[str] = []
    statuses: list[str] = []
    if observation.failed_batches:
        statuses.append("failing")
        reasons.append(f"{observation.failed_batches} batch(es) failed")
    if observation.dlq_batches:
        statuses.append("failing")
        reasons.append(f"{observation.dlq_batches} batch(es) quarantined to the DLQ")
    if (
        observation.lease_expires_at is not None
        and _parse(observation.lease_expires_at) <= reference
    ):
        statuses.append("stalled")
        reasons.append("a runner holds an expired lease (it likely died); it will be taken over")
    if lag is None:
        statuses.append("unknown")
        reasons.append("no batch has been committed yet")
    elif lag > max_lag_seconds:
        statuses.append("lagging")
        reasons.append(f"last commit {lag:.0f}s ago exceeds {max_lag_seconds:.0f}s")
    if observation.pending_batches:
        reasons.append(f"{observation.pending_batches} batch(es) written but not yet committed")
    if observation.late_rows:
        reasons.append(f"{observation.late_rows} late row(s) seen")

    status = min(statuses, key=STREAM_STATUSES.index) if statuses else "healthy"
    return StreamHealth(status, lag, max_lag_seconds, tuple(reasons))


@dataclass(frozen=True)
class RunObservation:
    run_id: str
    status: str
    attempt: int
    created_at: str | None
    updated_at: str | None


@dataclass(frozen=True)
class RunsSummary:
    total: int
    by_status: dict[str, int]
    retried: int
    last_success_at: str | None
    mean_duration_seconds: float | None
    core_seconds: float | None
    reasons: tuple[str, ...] = field(default=())

    @property
    def alert(self) -> bool:
        return any(self.by_status.get(status, 0) for status in _TERMINAL_BAD)


def summarize_runs(
    runs: Sequence[RunObservation], *, cores_per_run: int | None = None
) -> RunsSummary:
    """Status counts, retries, last success and an executor-core-seconds cost proxy.

    Duration is ``updated_at - created_at`` of finished runs: the coordinator's
    view, which includes queueing. ``core_seconds`` multiplies it by the
    profile's executors x cores when known — a relative cost signal, not a bill.
    """

    counts = Counter(run.status for run in runs)
    durations = [
        (_parse(run.updated_at) - _parse(run.created_at)).total_seconds()
        for run in runs
        if run.status == _TERMINAL_OK and run.created_at and run.updated_at
    ]
    successes = sorted(
        run.updated_at for run in runs if run.status == _TERMINAL_OK and run.updated_at
    )
    mean = sum(durations) / len(durations) if durations else None
    reasons = tuple(
        f"{counts[status]} run(s) {status}" for status in sorted(_TERMINAL_BAD) if counts[status]
    )
    return RunsSummary(
        total=len(runs),
        by_status=dict(sorted(counts.items())),
        retried=sum(1 for run in runs if run.attempt > 0),
        last_success_at=successes[-1] if successes else None,
        mean_duration_seconds=mean,
        core_seconds=sum(durations) * cores_per_run if cores_per_run and durations else None,
        reasons=reasons,
    )


def _parse(value: str) -> datetime:
    return _aware(datetime.fromisoformat(value))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
