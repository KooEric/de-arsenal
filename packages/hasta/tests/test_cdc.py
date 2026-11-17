from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest
from hasta.model import BatchLimits, ChangeEvent, MicroBatch, StreamPosition
from hasta.runner import HastaRunner, StreamRunner, StreamValidationError
from hasta.sources.outbox import PostgresOutboxSource
from hasta.spec import DatabaseOutboxSourceSpec, StreamSpec, TableCursorSourceSpec

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import DuckDBSinkSpec, PostgresSinkSpec
from arsenal_core.state import StateStore
from pugio.sinks.cdc import DuckDBChangeSink, PostgresChangeSink


def event(event_id: str, sequence: int, op: str, value: str | None = None) -> ChangeEvent:
    return ChangeEvent(
        event_id=event_id,
        sequence=sequence,
        op=op,  # type: ignore[arg-type]
        key={"id": 1},
        payload=None if op == "D" else {"id": 1, "value": value},
        occurred_at=datetime(2026, 8, 22, tzinfo=UTC),
    )


class Source:
    source_id = "test-outbox"

    def __init__(self, events: Sequence[ChangeEvent]) -> None:
        self.events = tuple(events)

    def poll(self, after: StreamPosition | None, limits: BatchLimits) -> MicroBatch:
        start = after or StreamPosition({"sequence": -1})
        available = tuple(item for item in self.events if item.sequence > (start.sequence or -1))
        picked = available[: limits.max_records]
        return MicroBatch(
            start=start,
            end=StreamPosition({"sequence": picked[-1].sequence if picked else start.sequence}),
            events=pa.table({}),
            event_hash="",
            source_exhausted=len(picked) < limits.max_records,
            changes=picked,
        )

    def close(self) -> None:
        pass


def test_crash_after_sink_write_replays_duplicate_event_without_duplicate_result(
    tmp_path: Path,
) -> None:
    state = StateStore(tmp_path / "state.sqlite")
    sink = DuckDBChangeSink(
        DuckDBSinkSpec(
            type="duckdb", path=str(tmp_path / "target.duckdb"), table="items", merge_key=["id"]
        )
    )

    def crash(phase: str) -> None:
        if phase == "after_sink_write":
            raise SystemExit("simulated crash")

    first = HastaRunner(
        state=state,
        pipeline="p",
        stream_name="s",
        revision="r",
        source=Source([event("e1", 1, "I", "one")]),
        sink=sink,
        fault_hook=crash,
    )
    with pytest.raises(SystemExit):
        first.run_once()
    assert state.checkpoint("p", "s") is None

    second = HastaRunner(
        state=state,
        pipeline="p",
        stream_name="s",
        revision="r",
        source=Source([event("e1", 1, "I", "one")]),
        sink=sink,
    )
    assert second.run_once()
    assert state.checkpoint("p", "s").position == {"sequence": 1}  # type: ignore[union-attr]
    with duckdb.connect(str(tmp_path / "target.duckdb")) as con:
        assert con.execute('SELECT id, value FROM "items"').fetchall() == [(1, "one")]
        assert con.execute('SELECT count(*) FROM "items__arsenal_cdc_events"').fetchone() == (1,)


def test_insert_update_delete_and_duplicate_event_leave_current_state_empty(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.sqlite")
    sink = DuckDBChangeSink(
        DuckDBSinkSpec(
            type="duckdb", path=str(tmp_path / "target.duckdb"), table="items", merge_key=["id"]
        )
    )
    events = [event("e1", 1, "I", "one"), event("e2", 2, "U", "two"), event("e3", 3, "D")]
    runner = HastaRunner(
        state=state, pipeline="p", stream_name="s", revision="r", source=Source(events), sink=sink
    )
    assert runner.run_once(BatchLimits(max_records=10))
    assert not runner.run_once(BatchLimits(max_records=10))
    with duckdb.connect(str(tmp_path / "target.duckdb")) as con:
        assert con.execute('SELECT count(*) FROM "items"').fetchone() == (0,)
        assert con.execute('SELECT count(*) FROM "items__arsenal_cdc_events"').fetchone() == (3,)


def test_duckdb_apply_rejects_conflicting_event_id_and_ignores_exact_replay(tmp_path: Path) -> None:
    sink = DuckDBChangeSink(
        DuckDBSinkSpec(
            type="duckdb", path=str(tmp_path / "target.duckdb"), table="items", merge_key=["id"]
        )
    )
    first = event("e1", 1, "I", "one")
    assert sink.apply_changes("batch-1", [first]).row_count == 1
    assert sink.apply_changes("batch-2", [first]).row_count == 0
    with pytest.raises(FatalError, match="conflicts"):
        sink.apply_changes("batch-3", [event("e1", 2, "U", "two")])


def cdc_spec(tmp_path: Path) -> StreamSpec:
    return StreamSpec.model_validate(
        {
            "name": "orders-cdc",
            "state_dir": str(tmp_path / "state"),
            "source": {
                "type": "database_outbox",
                "dialect": "postgres",
                "dsn_env": "APP_DATABASE_URL",
                "table": "public.order_outbox",
                "key_columns": ["id"],
            },
            "stream": {"name": "orders", "batch": {"max_records": 10}},
            "sink": {
                "type": "duckdb",
                "path": str(tmp_path / "target.duckdb"),
                "table": "items",
                "merge_key": ["id"],
            },
        }
    )


def test_stream_runner_uses_cdc_apply_path_and_checkpoint(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.sqlite")
    sink = DuckDBChangeSink(
        DuckDBSinkSpec(
            type="duckdb", path=str(tmp_path / "target.duckdb"), table="items", merge_key=["id"]
        )
    )
    runner = StreamRunner(
        cdc_spec(tmp_path),
        source=Source([event("e1", 1, "I", "one"), event("e2", 2, "U", "two")]),
        sink=sink,
        store=state,
        owner_id="cdc-test",
    )

    assert runner.run_once().committed == 1
    checkpoint = state.checkpoint("orders-cdc", "orders")
    assert checkpoint is not None and checkpoint.position == {"sequence": 2}
    with duckdb.connect(str(tmp_path / "target.duckdb")) as con:
        assert con.execute('SELECT id, value FROM "items"').fetchall() == [(1, "two")]


def test_stream_runner_rejects_out_of_order_outbox_page_before_sink_write(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.sqlite")
    sink = DuckDBChangeSink(
        DuckDBSinkSpec(
            type="duckdb", path=str(tmp_path / "target.duckdb"), table="items", merge_key=["id"]
        )
    )
    runner = StreamRunner(
        cdc_spec(tmp_path),
        source=Source([event("e2", 2, "U", "two"), event("e1", 1, "I", "one")]),
        sink=sink,
        store=state,
        owner_id="cdc-order-test",
    )

    with pytest.raises(StreamValidationError, match="strictly ordered"):
        runner.run_once()
    assert state.checkpoint("orders-cdc", "orders") is None


def test_table_cursor_requires_explicit_safety_declarations() -> None:
    with pytest.raises(ValueError, match="monotonically"):
        TableCursorSourceSpec(
            type="database_table_cursor",
            dialect="postgres",
            dsn_env="DSN",
            table="items",
            cursor_column="updated_at",
            primary_key=["id"],
            cursor_timezone="UTC",
            cursor_immutable_or_monotonic=False,
        )
    with pytest.warns(UserWarning, match="hard deletes"):
        TableCursorSourceSpec(
            type="database_table_cursor",
            dialect="postgres",
            dsn_env="DSN",
            table="items",
            cursor_column="updated_at",
            primary_key=["id"],
            cursor_timezone="UTC",
            cursor_immutable_or_monotonic=True,
        )


def test_outbox_envelope_rejects_bad_delete_payload_before_apply() -> None:
    source = PostgresOutboxSource(
        DatabaseOutboxSourceSpec(
            type="database_outbox",
            dialect="postgres",
            dsn_env="DSN",
            table="public.outbox",
            key_columns=["id"],
        )
    )
    with pytest.raises(FatalError, match="invalid immutable outbox event"):
        source.event(
            {
                "sequence": 1,
                "event_id": "e1",
                "op": "D",
                "id": 1,
                "payload": {"id": 1},
                "occurred_at": datetime(2026, 8, 22, tzinfo=UTC),
                "schema_version": None,
            }
        )


def test_postgres_cdc_apply_and_crash_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    require_docker: None,
) -> None:
    """Opt-in integration coverage for the Postgres ledger + target transaction."""

    pgtc = pytest.importorskip("testcontainers.postgres")
    psycopg = pytest.importorskip("psycopg")
    with pgtc.PostgresContainer("postgres:16-alpine") as container:
        monkeypatch.setenv("HASTA_CDC_PG_DSN", container.get_connection_url(driver=None))
        state = StateStore(tmp_path / "state.sqlite")
        sink = PostgresChangeSink(
            PostgresSinkSpec(
                type="postgres",
                dsn_env="HASTA_CDC_PG_DSN",
                table="items",
                merge_key=["id"],
            )
        )

        def crash(phase: str) -> None:
            if phase == "after_sink_write":
                raise SystemExit("simulated crash")

        with pytest.raises(SystemExit):
            HastaRunner(
                state=state,
                pipeline="p",
                stream_name="s",
                revision="r",
                source=Source([event("e1", 1, "I", "one")]),
                sink=sink,
                fault_hook=crash,
            ).run_once()
        assert state.checkpoint("p", "s") is None

        assert HastaRunner(
            state=state,
            pipeline="p",
            stream_name="s",
            revision="r",
            source=Source([event("e1", 1, "I", "one")]),
            sink=sink,
        ).run_once()
        final = HastaRunner(
            state=state,
            pipeline="p",
            stream_name="s",
            revision="r",
            source=Source(
                [
                    event("e1", 1, "I", "one"),
                    event("e2", 2, "U", "two"),
                    event("e3", 3, "D"),
                ]
            ),
            sink=sink,
        )
        assert final.run_once()
        with psycopg.connect(container.get_connection_url(driver=None)) as con, con.cursor() as cur:
            cur.execute('SELECT count(*) FROM "items"')
            assert cur.fetchone() == (0,)
            cur.execute('SELECT count(*) FROM "items__arsenal_cdc_events"')
            assert cur.fetchone() == (3,)
