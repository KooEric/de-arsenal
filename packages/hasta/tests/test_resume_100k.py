"""P2-1 DoD: 100,000 events through crashes and restarts — zero duplicates, zero gaps.

Real code paths end to end: ``RestPollingSource`` (over an in-process HTTP
transport), ``StreamRunner``, ``PugioStreamingSink`` → Parquet, and the
SQLite ledger. Crashes are injected at the two places that matter:

* after the sink wrote a batch but before the checkpoint commit, and
* after polling, before anything was written.

Each crash kills the runner (``SystemExit``); a brand-new runner with a new
owner id then resumes from the durable checkpoint.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
from hasta.model import BatchLimits, CommittedBatch, MicroBatch, StreamPosition
from hasta.runner import StreamRunner
from hasta.sink import PugioStreamingSink
from hasta.sources.rest_poll import RestPollingSource
from hasta.spec import RestPollSourceSpec, StreamSpec

from arsenal_core.execution import OutputProof
from arsenal_core.state import StateStore

TOTAL_EVENTS = 100_000
PAGE_SIZE = 1_000
SEED = 20261117
CRASHING_INCARNATIONS = 12


def make_spec(tmp_path: Path) -> StreamSpec:
    return StreamSpec.model_validate(
        {
            "name": "events",
            "state_dir": str(tmp_path / "state"),
            "source": {
                "type": "rest_poll",
                "url": "https://api.example.test/events",
                "limit_param": "limit",
                "cursor": {
                    "request_param": "after",
                    "response_path": "next",
                    "event_id_path": "id",
                },
                "record_path": "data",
            },
            "stream": {
                "name": "events",
                "poll_interval_seconds": 0.01,
                "batch": {"max_records": PAGE_SIZE, "max_bytes": 10_000_000, "max_wait_seconds": 1},
                "checkpoint": {"lease_ttl_seconds": 60},
            },
            "sink": {"type": "parquet", "path": str(tmp_path / "out")},
        }
    )


def events_api(request: httpx.Request) -> httpx.Response:
    """A cursor API over ids 0..TOTAL_EVENTS-1; the cursor is the next start id."""

    start = int(request.url.params.get("after", "0"))
    limit = int(request.url.params["limit"])
    stop = min(start + limit, TOTAL_EVENTS)
    data = [
        {"id": i, "value": i * 3, "kind": "even" if i % 2 == 0 else "odd"}
        for i in range(start, stop)
    ]
    return httpx.Response(
        200, json={"data": data, "next": str(stop) if stop < TOTAL_EVENTS else None}
    )


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 17, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class Crash(SystemExit):
    pass


class CrashAfterWrite:
    """Delegates to the real sink, then dies before the runner can commit."""

    def __init__(self, inner: PugioStreamingSink, crash_on: set[int]) -> None:
        self.inner = inner
        self.capabilities = inner.capabilities
        self.crash_on = crash_on
        self.calls = 0

    def write_batch(self, batch: CommittedBatch, table: pa.Table) -> OutputProof:
        self.calls += 1
        proof = self.inner.write_batch(batch, table)
        if self.calls in self.crash_on:
            raise Crash(f"crash after sink write #{self.calls}")
        return proof


class CrashAfterPoll:
    """Delegates to the real REST source, then dies before anything is written."""

    def __init__(self, inner: RestPollingSource, crash_on: set[int]) -> None:
        self.inner = inner
        self.source_id = inner.source_id
        self.crash_on = crash_on
        self.calls = 0

    def poll(self, after: StreamPosition | None, limits: BatchLimits) -> MicroBatch:
        self.calls += 1
        batch = self.inner.poll(after, limits)
        if self.calls in self.crash_on:
            raise Crash(f"crash after poll #{self.calls}")
        return batch

    def close(self) -> None:
        self.inner.close()


def test_100k_events_survive_crashes_with_no_duplicates_or_gaps(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    rng = random.Random(SEED)
    clock = Clock()
    store = StateStore(Path(spec.state_dir) / "events.db", clock=clock)
    client = httpx.Client(transport=httpx.MockTransport(events_api))
    assert isinstance(spec.source, RestPollSourceSpec)
    source_spec = spec.source
    restarts = 0
    crashes_after_write = 0

    while True:
        # The first CRASHING_INCARNATIONS runners all die at a random batch,
        # mostly after a sink write; the last one runs to completion.
        write_crashes: set[int] = set()
        poll_crashes: set[int] = set()
        if restarts < CRASHING_INCARNATIONS:
            target = write_crashes if rng.random() < 0.75 else poll_crashes
            target.add(rng.randint(1, 12))
        sink = CrashAfterWrite(PugioStreamingSink(spec.sink), write_crashes)
        source = CrashAfterPoll(RestPollingSource(source_spec, client=client), poll_crashes)
        runner = StreamRunner(
            spec, source=source, sink=sink, store=store, owner_id=f"runner-{restarts}"
        )
        try:
            report = runner.run()
        except Crash as crash:
            restarts += 1
            crashes_after_write += "sink write" in str(crash)
            # An in-process crash unwinds through ``run``'s finally and releases
            # the lease; a SIGKILL would leave it behind. Cover both: if a lease
            # survived, let it expire so the next incarnation takes over fenced.
            lease = store.lease(runner.resource)
            if lease is not None:
                clock.now = lease.expires_at + timedelta(seconds=1)
            if rng.random() < 0.5:
                # Simulate the SIGKILL case on half of the restarts: a stale lease
                # held by the dead owner that must expire before takeover.
                store.acquire_lease(runner.resource, f"dead-{restarts}", 30)
                clock.now += timedelta(seconds=31)
            assert restarts < 400, "runner never converged"
            continue
        assert report.exhausted
        break

    table = cast(
        pa.Table,
        pq.read_table(str(tmp_path / "out"), columns=["id"]),  # pyright: ignore[reportUnknownMemberType]
    )
    values = cast(list[int], table.column("id").to_pylist())

    assert restarts == CRASHING_INCARNATIONS
    assert crashes_after_write >= 3, "must cover the write-then-crash window"
    assert len(values) == TOTAL_EVENTS, f"{len(values) - TOTAL_EVENTS:+d} rows vs expected"
    assert len(set(values)) == TOTAL_EVENTS, "duplicate events reached the sink"
    assert set(values) == set(range(TOTAL_EVENTS)), "gap: some events never arrived"

    summary = store.stream_batch_summary("events", "events")
    assert summary.by_status == {"committed": TOTAL_EVENTS // PAGE_SIZE}
    assert summary.rows == TOTAL_EVENTS
