"""One JSON-ready view of a stream for operators and the umbrella CLI."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scorpio import StreamObservation, assess_stream

from arsenal_core.state import StateStore
from hasta.spec import StreamSpec
from pugio.dlq import dlq_dir

_MIN_DEFAULT_LAG_SECONDS = 60.0
_POLL_INTERVALS_BEFORE_LAGGING = 5


def state_path(spec: StreamSpec) -> Path:
    return Path(spec.state_dir) / f"{spec.name}.db"


def stream_resource(spec: StreamSpec) -> str:
    return f"stream:{spec.name}:{spec.stream.name}"


def default_max_lag_seconds(spec: StreamSpec) -> float:
    """Five missed polls, but never less than a minute."""

    return max(
        _MIN_DEFAULT_LAG_SECONDS,
        _POLL_INTERVALS_BEFORE_LAGGING * spec.stream.poll_interval_seconds,
    )


def dlq_batches(spec: StreamSpec) -> int:
    directory = dlq_dir(spec.state_dir, spec.name)
    return sum(1 for _ in directory.glob("*.json")) if directory.is_dir() else 0


def stream_status(
    spec: StreamSpec,
    store: StateStore,
    *,
    max_lag_seconds: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    reference = now or datetime.now(UTC)
    checkpoint = store.checkpoint(spec.name, spec.stream.name)
    lease = store.lease(stream_resource(spec))
    summary = store.stream_batch_summary(spec.name, spec.stream.name)
    quarantined = dlq_batches(spec)
    health = assess_stream(
        StreamObservation(
            last_commit_at=checkpoint.updated_at if checkpoint is not None else None,
            lease_expires_at=lease.expires_at.isoformat() if lease is not None else None,
            failed_batches=summary.by_status.get("failed", 0),
            pending_batches=summary.by_status.get("planned", 0),
            dlq_batches=quarantined,
            late_rows=summary.late_rows,
        ),
        max_lag_seconds=max_lag_seconds or default_max_lag_seconds(spec),
        now=reference,
    )
    return {
        "stream": {"pipeline": spec.name, "name": spec.stream.name},
        "health": {
            "status": health.status,
            "alert": health.alert,
            "lag_seconds": None if health.lag_seconds is None else round(health.lag_seconds, 1),
            "max_lag_seconds": health.max_lag_seconds,
            "reasons": list(health.reasons),
        },
        "checkpoint": None
        if checkpoint is None
        else {
            "generation": checkpoint.generation,
            "position": dict(checkpoint.position),
            "watermark": checkpoint.watermark,
            "last_batch_id": checkpoint.last_batch_id,
            "updated_at": checkpoint.updated_at,
        },
        "lease": None
        if lease is None
        else {
            "owner_id": lease.owner_id,
            "fence": lease.fence,
            "expires_at": lease.expires_at.isoformat(),
            "expired": lease.is_expired(reference),
            "seconds_remaining": max(0.0, round((lease.expires_at - reference).total_seconds(), 1)),
        },
        "batches": {
            "by_status": dict(summary.by_status),
            "rows": summary.rows,
            "bytes": summary.bytes,
            "late_rows": summary.late_rows,
            "dlq": quarantined,
        },
        "sink": {"type": spec.sink.type, "target": sink_target(spec)},
    }


def sink_target(spec: StreamSpec) -> str:
    sink = spec.sink.model_dump()
    if spec.sink.type == "duckdb":
        return f"{sink['path']}#{sink['table']}"
    if spec.sink.type == "postgres":
        return f"${sink['dsn_env']}#{sink['table']}"  # env var name, never the DSN
    return str(sink["path"])
