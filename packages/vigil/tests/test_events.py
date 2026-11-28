from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from _events import SCHEMA, write_events

from arsenal_core.errors import FatalError
from vigil.events import EVENT_COLUMNS, empty_events, fixture_events, load_events


def test_empty_events_has_every_column_and_no_rows() -> None:
    con = empty_events()
    names = [row[0] for row in con.execute("DESCRIBE events").fetchall()]
    assert names == list(EVENT_COLUMNS)
    assert con.execute("SELECT count(*) FROM events").fetchone() == (0,)


def test_fixture_events_fills_id_time_and_nulls() -> None:
    con = fixture_events([{"action": "ConsoleLogin"}, {"event_id": "mine", "action": "X"}])
    rows = con.execute("SELECT event_id, action, actor_id FROM events ORDER BY action").fetchall()
    assert rows == [("fixture-0", "ConsoleLogin", None), ("mine", "X", None)]


def test_fixture_events_rejects_unknown_column() -> None:
    with pytest.raises(FatalError, match="unknown columns"):
        fixture_events([{"acton": "typo"}])


def test_connections_cannot_read_outside_events(tmp_path: Path) -> None:
    outside = tmp_path / "secret.csv"
    outside.write_text("a\n1\n", encoding="utf-8")
    con = empty_events()
    with pytest.raises(duckdb.Error):
        con.execute(f"SELECT * FROM read_csv('{outside}')")
    with pytest.raises(duckdb.Error):
        con.execute("SET enable_external_access=true")


def test_load_events_connection_is_locked(tmp_path: Path) -> None:
    write_events(tmp_path / "a.parquet", [{"event_id": "e1"}])
    con = load_events(str(tmp_path))
    with pytest.raises(duckdb.Error):
        con.execute(f"SELECT * FROM read_csv('{tmp_path / 'a.parquet'}')")
    con.close()


def test_load_events_reads_a_directory(tmp_path: Path) -> None:
    write_events(tmp_path / "events" / "a.parquet", [{"event_id": "e1", "action": "A"}])
    write_events(
        tmp_path / "events" / "b.parquet",
        [{"event_id": "e2", "action": "B", "event_time": datetime(2026, 1, 2)}],
    )
    con = load_events(str(tmp_path / "events"))
    assert con.execute("SELECT count(*) FROM events").fetchone() == (2,)


def test_load_events_without_files_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="no event files"):
        load_events(str(tmp_path / "*.parquet"))


def test_load_events_names_missing_columns(tmp_path: Path) -> None:
    pq.write_table(pa.table({"event_id": ["e1"]}), tmp_path / "thin.parquet")  # pyright: ignore[reportUnknownMemberType]
    with pytest.raises(FatalError, match="missing columns"):
        load_events(str(tmp_path / "thin.parquet"))


def test_load_events_names_columns_with_the_wrong_type(tmp_path: Path) -> None:
    columns: dict[str, list[object]] = {name: ["x"] for name in EVENT_COLUMNS}
    columns["mfa_used"] = [True]
    pq.write_table(pa.table(columns), tmp_path / "text-time.parquet")  # pyright: ignore[reportUnknownMemberType]
    with pytest.raises(FatalError, match="event_time is VARCHAR, expected TIMESTAMP"):
        load_events(str(tmp_path / "text-time.parquet"))


def test_load_events_rejects_a_timezone_aware_event_time(tmp_path: Path) -> None:
    aware = datetime(2026, 1, 1, tzinfo=UTC)
    columns: dict[str, list[object]] = {name: ["x"] for name in EVENT_COLUMNS}
    columns["mfa_used"] = [True]
    columns["event_time"] = [aware]
    pq.write_table(pa.table(columns), tmp_path / "tz.parquet")  # pyright: ignore[reportUnknownMemberType]
    with pytest.raises(FatalError, match="event_time is TIMESTAMP WITH TIME ZONE"):
        load_events(str(tmp_path / "tz.parquet"))


def test_load_events_accepts_a_nanosecond_event_time(tmp_path: Path) -> None:
    fields = [
        pa.field(field.name, pa.timestamp("ns") if field.name == "event_time" else field.type)
        for field in SCHEMA
    ]
    row = {**dict.fromkeys(SCHEMA.names), "event_id": "e1", "event_time": datetime(2026, 1, 1)}
    table = pa.Table.from_pylist([row], schema=pa.schema(fields))
    pq.write_table(table, tmp_path / "ns.parquet")  # pyright: ignore[reportUnknownMemberType]
    con = load_events(str(tmp_path / "ns.parquet"))
    assert con.execute("SELECT count(*) FROM events").fetchone() == (1,)
