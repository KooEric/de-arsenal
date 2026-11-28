from datetime import datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from _events import write_events

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
