"""Fence a pipeline run against directory-rewriting maintenance (Onager).

A run holds the lease ``pipeline:<name>`` in its own state DB and registers
itself as a writer of a local Parquet sink (``arsenal_core.writers``). Onager
takes every registered writer's lease before compacting, so:

* compaction refuses while a run is live;
* a run refuses to start while compaction holds the lease;
* two runs of the same pipeline cannot race each other.

The lease is short (``LEASE_TTL_SECONDS``) and a heartbeat thread renews it on
its own SQLite connection, so a slow unit keeps it and a hard-killed run
(``kill -9``, power loss) stops blocking a rerun within one TTL. Ctrl-C and
exceptions release it immediately. If the heartbeat ever loses the lease, the
run stops after the current unit instead of writing into a directory that may
be mid-swap; finished units stay done and a rerun resumes.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from arsenal_core.errors import FatalError
from arsenal_core.execution import LeaseToken
from arsenal_core.spec.models import PipelineSpec
from arsenal_core.state import StateStore
from arsenal_core.writers import StreamWriter, register_stream_writer

LEASE_TTL_SECONDS = 60.0
HEARTBEAT_SECONDS = 15.0


def resource(spec: PipelineSpec) -> str:
    return f"pipeline:{spec.name}"


def state_path(spec: PipelineSpec) -> Path:
    return Path(spec.state_dir) / f"{spec.name}.db"


def _register(spec: PipelineSpec) -> None:
    sink = spec.sink
    if sink.type != "parquet" or "://" in sink.path:
        return  # remote objects and databases are not rewritten in place by Onager
    register_stream_writer(
        Path(sink.path),
        StreamWriter(
            state_db=str(state_path(spec).resolve()),
            resource=resource(spec),
            pipeline=spec.name,
            stream_name=spec.name,
        ),
    )


class _Heartbeat(threading.Thread):
    def __init__(self, db: Path, token: LeaseToken, ttl: float, every: float) -> None:
        super().__init__(name=f"pugio-lease-{token.resource}", daemon=True)
        self.token = token
        self.lost = False
        self._db, self._ttl, self._every = db, ttl, every
        self._stop_event = threading.Event()

    def run(self) -> None:
        store = StateStore(self._db)
        try:
            while not self._stop_event.wait(self._every):
                renewed = store.renew_lease(self.token, self._ttl)
                if renewed is None:
                    self.lost = True
                    return
                self.token = renewed
        finally:
            store.close()

    def stop(self) -> LeaseToken:
        self._stop_event.set()
        self.join()
        return self.token


@contextmanager
def hold_lease(
    store: StateStore,
    db: Path,
    resource_name: str,
    *,
    busy: str,
    detail: str,
    lost: str,
    ttl_seconds: float = LEASE_TTL_SECONDS,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
) -> Generator[Callable[[], None], None, None]:
    """Hold ``resource_name`` with a heartbeat; yields ``check()`` to call between units.

    A held lease fails with ``{busy} (lease held by ...){detail}``; a lost one with ``lost``.
    """

    token = store.acquire_lease(resource_name, f"pugio-{uuid.uuid4()}", ttl_seconds)
    if token is None:
        holder = store.lease(resource_name)
        who = holder.owner_id if holder is not None else "another process"
        until = f" until {holder.expires_at}" if holder is not None else ""
        raise FatalError(f"{busy} (lease held by {who}{until}){detail}")
    heartbeat = _Heartbeat(db, token, ttl_seconds, heartbeat_seconds)
    heartbeat.start()

    def check() -> None:
        if heartbeat.lost:
            raise FatalError(lost)

    try:
        yield check
    finally:
        store.release_lease(heartbeat.stop())


@contextmanager
def pipeline_lease(
    spec: PipelineSpec,
    store: StateStore,
    *,
    ttl_seconds: float = LEASE_TTL_SECONDS,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
) -> Generator[Callable[[], None], None, None]:
    """Hold the run lease; yields ``check()`` to call between units."""

    _register(spec)
    with hold_lease(
        store,
        state_path(spec),
        resource(spec),
        busy=f"pipeline {spec.name!r} is busy",
        detail=(
            ": another run or a compaction of its output is in progress. "
            "A crashed run's lease expires by itself"
        ),
        lost=(
            f"pipeline {spec.name!r} lost its run lease; stopped to avoid racing "
            "maintenance on its output. Rerun to resume"
        ),
        ttl_seconds=ttl_seconds,
        heartbeat_seconds=heartbeat_seconds,
    ) as check:
        yield check
