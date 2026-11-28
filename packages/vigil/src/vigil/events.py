"""The `events` table that rules read, and the locked connections that hold it.

Every connection returned here has external access switched off, so a rule query can read
`events` and nothing else on disk or over the network. DuckDB does not let the setting be
switched back on while the connection is open.
"""

from __future__ import annotations

import glob as globlib
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import duckdb

from arsenal_core.errors import FatalError

EVENT_COLUMNS: Final[dict[str, str]] = {
    "event_id": "VARCHAR",
    "event_time": "TIMESTAMP",
    "source": "VARCHAR",
    "service": "VARCHAR",
    "action": "VARCHAR",
    "actor_type": "VARCHAR",
    "actor_id": "VARCHAR",
    "src_ip": "VARCHAR",
    "region": "VARCHAR",
    "outcome": "VARCHAR",
    "mfa_used": "BOOLEAN",
    "resource": "VARCHAR",
    "raw": "VARCHAR",
}

# Other DuckDB types accepted for a column. A nanosecond timestamp (Parquet written by
# pandas, for one) runs through `vigil run` end to end; alerts keep microsecond precision.
ACCEPTED_TYPES: Final[dict[str, frozenset[str]]] = {"event_time": frozenset({"TIMESTAMP_NS"})}

# event_time given to a fixture event that does not set one.
FIXTURE_TIME: Final = datetime(2026, 1, 1)


def _lock(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("SET enable_external_access=false")


def empty_events() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    columns = ", ".join(f"{name} {kind}" for name, kind in EVENT_COLUMNS.items())
    con.execute(f"CREATE TABLE events ({columns})")
    _lock(con)
    return con


def fixture_events(rows: list[dict[str, Any]]) -> duckdb.DuckDBPyConnection:
    """`events` holding exactly `rows`. Columns a row leaves out are NULL.

    Raises FatalError for an unknown column and duckdb.Error for a value of the wrong type;
    the connection is closed first in both cases.
    """
    con = empty_events()
    placeholders = ", ".join("?" for _ in EVENT_COLUMNS)
    for index, row in enumerate(rows):
        unknown = sorted(set(row) - set(EVENT_COLUMNS))
        if unknown:
            con.close()
            raise FatalError(f"fixture event has unknown columns: {unknown}")
        full: dict[str, Any] = dict.fromkeys(EVENT_COLUMNS)
        full["event_id"] = f"fixture-{index}"
        full["event_time"] = FIXTURE_TIME
        full.update(row)
        try:
            con.execute(f"INSERT INTO events VALUES ({placeholders})", list(full.values()))
        except duckdb.Error:
            con.close()
            raise
    return con


def resolve_event_files(pattern: str) -> list[str]:
    """A directory means every *.parquet under it; anything else is a glob."""
    path = Path(pattern)
    if path.is_dir():
        files = sorted(str(p) for p in path.rglob("*.parquet"))
    else:
        files = sorted(globlib.glob(pattern, recursive=True))
    if not files:
        raise FatalError(f"no event files match {pattern}")
    return files


def load_events(pattern: str) -> duckdb.DuckDBPyConnection:
    """Copy the lake into an in-memory `events` table, then lock the connection."""
    files = resolve_event_files(pattern)
    listing = ", ".join("'" + name.replace("'", "''") + "'" for name in files)
    con = duckdb.connect()
    try:
        con.execute(
            f"CREATE TABLE events AS SELECT * FROM read_parquet([{listing}], union_by_name=true)"
        )
    except duckdb.Error as e:
        con.close()
        raise FatalError(f"cannot read events from {pattern}: {e}") from e
    found = {str(row[0]): str(row[1]) for row in con.execute("DESCRIBE events").fetchall()}
    missing = [name for name in EVENT_COLUMNS if name not in found]
    if missing:
        con.close()
        raise FatalError(f"events is missing columns: {missing}")
    wrong = [
        f"{name} is {found[name]}, expected {kind}"
        for name, kind in EVENT_COLUMNS.items()
        if found[name] != kind and found[name] not in ACCEPTED_TYPES.get(name, frozenset())
    ]
    if wrong:
        con.close()
        raise FatalError(f"events column types differ: {'; '.join(wrong)}")
    _lock(con)
    return con
