from datetime import UTC, datetime
from typing import Any

import pytest
from scorpio import (
    RunObservation,
    StreamHealth,
    StreamObservation,
    assess_stream,
    summarize_runs,
)

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def health(**overrides: Any) -> StreamHealth:
    fields: dict[str, Any] = {"last_commit_at": "2026-11-17T11:59:30+00:00", **overrides}
    return assess_stream(StreamObservation(**fields), max_lag_seconds=60, now=NOW)


def test_recent_commit_is_healthy() -> None:
    result = health()

    assert result.status == "healthy" and not result.alert
    assert result.lag_seconds == 30


def test_old_commit_is_lagging() -> None:
    result = health(last_commit_at="2026-11-17T11:50:00+00:00")

    assert result.status == "lagging" and result.alert
    assert "600s" in result.reasons[0]


def test_never_committed_is_unknown_not_alerting() -> None:
    result = health(last_commit_at=None)

    assert result.status == "unknown" and not result.alert


def test_expired_lease_means_a_dead_runner() -> None:
    result = health(lease_expires_at="2026-11-17T11:59:00+00:00")

    assert result.status == "stalled"


def test_live_lease_is_fine() -> None:
    assert health(lease_expires_at="2026-11-17T12:00:30+00:00").status == "healthy"


@pytest.mark.parametrize("field", ["failed_batches", "dlq_batches"])
def test_failures_and_quarantine_dominate_other_states(field: str) -> None:
    result = health(
        **{field: 2},
        last_commit_at="2026-11-17T11:00:00+00:00",
        lease_expires_at="2026-11-17T11:00:00+00:00",
    )

    assert result.status == "failing"
    assert len(result.reasons) == 3  # failing + stalled + lagging all reported


def test_pending_and_late_rows_are_informational() -> None:
    result = health(pending_batches=1, late_rows=4)

    assert result.status == "healthy"
    assert any("not yet committed" in r for r in result.reasons)
    assert any("4 late" in r for r in result.reasons)


def test_naive_timestamps_are_treated_as_utc() -> None:
    assert health(last_commit_at="2026-11-17T11:59:30").lag_seconds == 30


def test_max_lag_must_be_positive() -> None:
    with pytest.raises(ValueError):
        assess_stream(StreamObservation(last_commit_at=None), max_lag_seconds=0)


def run(run_id: str, status: str, attempt: int = 0, minutes: int = 2) -> RunObservation:
    return RunObservation(
        run_id,
        status,
        attempt,
        "2026-11-17T10:00:00+00:00",
        f"2026-11-17T10:{minutes:02d}:00+00:00",
    )


def test_runs_summary_counts_retries_successes_and_cost() -> None:
    summary = summarize_runs(
        [
            run("a", "succeeded", minutes=2),
            run("b", "succeeded", 1, minutes=4),
            run("c", "running"),
        ],
        cores_per_run=4,
    )

    assert summary.total == 3
    assert summary.by_status == {"running": 1, "succeeded": 2}
    assert summary.retried == 1
    assert summary.last_success_at == "2026-11-17T10:04:00+00:00"
    assert summary.mean_duration_seconds == 180
    assert summary.core_seconds == 360 * 4
    assert not summary.alert


def test_failed_or_lost_runs_alert() -> None:
    summary = summarize_runs([run("a", "failed"), run("b", "lost")])

    assert summary.alert
    assert summary.reasons == ("1 run(s) failed", "1 run(s) lost")
    assert summary.core_seconds is None and summary.mean_duration_seconds is None


def test_empty_history() -> None:
    summary = summarize_runs([])

    assert summary.total == 0 and summary.last_success_at is None and not summary.alert
