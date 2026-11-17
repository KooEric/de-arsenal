"""Focused REST microbatch restart, fencing, contract, and identity coverage."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pyarrow as pa
import pytest
from hasta.cli import app
from hasta.model import (
    BatchLimits,
    CdcEvent,
    CommittedBatch,
    MicroBatch,
    StreamPosition,
    batch_identity,
)
from hasta.runner import StreamRunner, StreamValidationError
from hasta.sources.rest_poll import RestPollingSource
from hasta.spec import DatabaseOutboxSourceSpec, RestPollSourceSpec, StreamSpec
from typer.testing import CliRunner

from arsenal_core.execution import ArtifactRef, OutputProof
from arsenal_core.state import LeaseLostError, StateStore


def spec(tmp_path: Path, **overrides: object) -> StreamSpec:
    raw: dict[str, object] = {
        "name": "orders",
        "state_dir": str(tmp_path / "state"),
        "source": {
            "type": "rest_poll",
            "url": "https://api.example.test/orders",
            "cursor": {
                "request_param": "after",
                "response_path": "next_cursor",
                "event_id_path": "id",
            },
            "record_path": "data",
        },
        "stream": {
            "name": "orders",
            "poll_interval_seconds": 0.01,
            "batch": {"max_records": 10, "max_bytes": 100_000, "max_wait_seconds": 1},
            "checkpoint": {"lease_ttl_seconds": 60},
        },
        "sink": {"type": "parquet", "path": str(tmp_path / "out")},
    }
    raw.update(overrides)
    return StreamSpec.model_validate(raw)


def proof(batch: CommittedBatch, rows: int) -> OutputProof:
    now = datetime.now(UTC)
    return OutputProof(
        run_id=batch.batch_id,
        revision=batch.revision,
        output=ArtifactRef(uri="memory://test", format="parquet"),
        input_versions=(batch.source_id,),
        row_count=rows,
        schema_hash="schema",
        plan_hash=batch.revision,
        started_at=now,
        finished_at=now,
    )


class Source:
    source_id = "test-source"

    def __init__(self, table: pa.Table, *, exhausted: bool = True) -> None:
        self.table = table
        self.exhausted = exhausted
        self.after: list[StreamPosition | None] = []

    def poll(self, after: StreamPosition | None, limits: BatchLimits) -> MicroBatch:
        self.after.append(after)
        start = after or StreamPosition({})
        end = StreamPosition({"cursor": "done", "_hasta_exhausted": self.exhausted})
        return MicroBatch(start, end, self.table, "events-sha", self.exhausted)

    def close(self) -> None:
        pass


class IdempotentSink:
    def __init__(self, after_write: Callable[[], None] | None = None) -> None:
        self.after_write = after_write
        self.writes: list[str] = []
        self.visible: dict[str, int] = {}

    def write_batch(self, batch: CommittedBatch, table: pa.Table) -> OutputProof:
        self.writes.append(batch.batch_id)
        self.visible[batch.batch_id] = table.num_rows
        if self.after_write is not None:
            self.after_write()
        return proof(batch, table.num_rows)


def test_rest_poll_passes_cursor_and_hashes_stable_event_ids() -> None:
    request_params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_params.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={"data": [{"id": "b"}, {"id": "a"}], "next_cursor": "c2"},
        )

    source = RestPollingSource(
        RestPollSourceSpec.model_validate(
            {
                "type": "rest_poll",
                "url": "https://api.example.test/orders",
                "limit_param": "limit",
                "cursor": {
                    "request_param": "after",
                    "response_path": "next_cursor",
                    "event_id_path": "id",
                },
            }
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    batch = source.poll(StreamPosition({"cursor": "c1"}), BatchLimits(10, 100_000, 1))

    assert request_params == [{"after": "c1", "limit": "10"}]
    assert batch.end.value == {"cursor": "c2", "_hasta_exhausted": False}
    assert batch.event_hash == "02d8bc3008a9bb0dcc4b86d7fd3428ced792355c733c19756bec5a56dc61b2c5"


def test_batch_identity_is_stable_across_mapping_order() -> None:
    first = batch_identity(
        pipeline="p",
        stream_name="s",
        revision="r",
        source_id="source",
        start=StreamPosition({"cursor": "a", "partition": 1}),
        end=StreamPosition({"cursor": "b", "partition": 1}),
        event_hash="events",
    )
    second = batch_identity(
        pipeline="p",
        stream_name="s",
        revision="r",
        source_id="source",
        start=StreamPosition({"partition": 1, "cursor": "a"}),
        end=StreamPosition({"partition": 1, "cursor": "b"}),
        event_hash="events",
    )

    assert first == second


def test_restart_after_sink_write_reuses_batch_identity_and_advances_once(tmp_path: Path) -> None:
    stream_spec = spec(tmp_path)
    store = StateStore(tmp_path / "state.db")
    source = Source(pa.table({"id": ["a"], "value": [1]}))
    crashed = IdempotentSink(after_write=lambda: (_ for _ in ()).throw(SystemExit("crash")))
    first = StreamRunner(stream_spec, source=source, sink=crashed, store=store, owner_id="first")

    with pytest.raises(SystemExit, match="crash"):
        first.run_once()

    assert store.checkpoint("orders", "orders") is None
    assert len(crashed.visible) == 1
    retry = IdempotentSink()
    second = StreamRunner(stream_spec, source=source, sink=retry, store=store, owner_id="second")
    report = second.run_once()
    checkpoint = store.checkpoint("orders", "orders")

    assert report.committed == 1
    assert checkpoint is not None
    assert checkpoint.position["cursor"] == "done"
    assert list(crashed.visible) == list(retry.visible)
    assert store.stream_batch(next(iter(retry.visible))).status == "committed"  # type: ignore[union-attr]


def test_fenced_runner_cannot_commit_after_its_lease_is_taken(tmp_path: Path) -> None:
    current = datetime(2026, 1, 1, tzinfo=UTC)

    def clock() -> datetime:
        return current

    stream_spec = spec(tmp_path)
    store = StateStore(tmp_path / "fence.db", clock=clock)
    source = Source(pa.table({"id": ["a"]}))

    def steal_lease() -> None:
        nonlocal current
        current += timedelta(seconds=61)
        assert store.acquire_lease("stream:orders:orders", "new-owner", 60) is not None

    runner = StreamRunner(
        stream_spec,
        source=source,
        sink=IdempotentSink(after_write=steal_lease),
        store=store,
        owner_id="stale-owner",
    )

    with pytest.raises(LeaseLostError):
        runner.run_once()

    assert store.checkpoint("orders", "orders") is None


def test_contract_quarantine_writes_pugio_dlq_and_keeps_checkpoint(tmp_path: Path) -> None:
    stream_spec = spec(
        tmp_path,
        contract={
            "columns": [{"name": "id", "type": "string", "nullable": False}],
            "on_violation": "quarantine",
        },
    )
    store = StateStore(tmp_path / "contract.db")
    runner = StreamRunner(
        stream_spec,
        source=Source(pa.table({"wrong": ["x"]})),
        sink=IdempotentSink(),
        store=store,
        owner_id="contract-owner",
    )

    with pytest.raises(StreamValidationError, match="failed validation"):
        runner.run_once()

    assert store.checkpoint("orders", "orders") is None
    assert list((tmp_path / "state" / "dlq" / "orders").glob("*.parquet"))


def test_local_pugio_parquet_sink_uses_the_batch_id_as_its_file_identity(tmp_path: Path) -> None:
    stream_spec = spec(tmp_path)
    runner = StreamRunner(
        stream_spec,
        source=Source(pa.table({"id": ["a"], "value": [1]})),
        owner_id="pugio-adapter",
    )
    try:
        assert runner.run_once().committed == 1
    finally:
        runner.close()

    files = list((tmp_path / "out").glob("*.parquet"))
    assert len(files) == 1
    assert len(files[0].stem) == 64


def test_cdc_models_validate_the_outbox_envelope_shape() -> None:
    source = DatabaseOutboxSourceSpec.model_validate(
        {
            "type": "database_outbox",
            "dialect": "postgres",
            "dsn_env": "APP_DATABASE_URL",
            "table": "public.order_outbox",
            "key_columns": ["order_id"],
        }
    )
    event = CdcEvent(
        event_id="evt-1",
        sequence=1,
        op="I",
        key={"order_id": "o-1"},
        payload={"order_id": "o-1"},
        occurred_at=datetime(2026, 8, 22, tzinfo=UTC),
    )

    assert source.table == "public.order_outbox"
    assert event.sequence == 1
    with pytest.raises(ValueError, match="must not include"):
        CdcEvent(
            event_id="evt-2",
            sequence=2,
            op="D",
            key={"order_id": "o-1"},
            payload={"order_id": "o-1"},
            occurred_at=datetime(2026, 8, 22, tzinfo=UTC),
        )


def test_cli_once_and_max_batches_select_the_expected_runner_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, int | None, bool | None]] = []

    class FakeRunner:
        def __init__(self, loaded_spec: StreamSpec, **_kwargs: object) -> None:
            assert loaded_spec.name == "orders"

        def run_once(self) -> object:
            calls.append(("once", None, None))
            return type("Report", (), {"__dict__": {"committed": 1}})()

        def run(self, *, max_batches: int | None, wait_between_polls: bool) -> object:
            calls.append(("run", max_batches, wait_between_polls))
            return type("Report", (), {"__dict__": {"committed": 1}})()

        def close(self) -> None:
            calls.append(("close", None, None))

    def fake_load(_: Path) -> StreamSpec:
        return spec(tmp_path)

    monkeypatch.setattr("hasta.cli.load_stream", fake_load)
    monkeypatch.setattr("hasta.cli.StreamRunner", FakeRunner)
    cli = CliRunner()

    once = cli.invoke(app, ["run", "stream.yaml", "--once"])
    maximum = cli.invoke(app, ["run", "stream.yaml", "--max-batches", "2"])
    conflict = cli.invoke(
        app,
        ["run", "stream.yaml", "--once", "--max-batches", "2"],
    )

    assert once.exit_code == 0
    assert maximum.exit_code == 0
    assert conflict.exit_code == 2
    assert "cannot be used together" in conflict.output
    assert calls == [
        ("once", None, None),
        ("close", None, None),
        ("run", 2, True),
        ("close", None, None),
    ]
