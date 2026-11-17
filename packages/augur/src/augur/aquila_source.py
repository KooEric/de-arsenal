"""Read table locations from an Aquila catalog (AQ-ADR-4).

Aquila is the system of record; Augur only reads ``aquila.db`` — through plain
sqlite, read-only, so ``de-augur`` keeps no dependency on ``de-aquila``. Without
a catalog file, ``augur index SOURCES.yaml`` works exactly as before.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from arsenal_core.errors import FatalError

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _glob(directory: Path) -> str | None:
    if any(directory.glob("*.parquet")):
        return f"{directory.as_posix()}/*.parquet"
    if any(directory.rglob("*.parquet")):
        return f"{directory.as_posix()}/**/*.parquet"
    return None


def tables_from_aquila(db: Path) -> dict[str, str]:
    """``{dataset name: parquet glob}`` for every active local Parquet dataset that exists."""

    if not db.is_file():
        raise FatalError(f"aquila catalog not found: {db} (run `aquila scan` first)")
    try:
        con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT name, uri FROM datasets WHERE format='parquet' AND status='active' "
                "AND uri LIKE 'file://%' ORDER BY name"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        raise FatalError(f"cannot read aquila catalog {db}: {exc}") from exc
    tables: dict[str, str] = {}
    for name, uri in rows:
        location = Path(str(uri).removeprefix("file://"))
        if not _IDENTIFIER.match(str(name)):
            continue  # Augur table names must be SQL identifiers
        if location.is_file() and location.suffix == ".parquet":
            tables[str(name)] = location.as_posix()
        elif location.is_dir() and (pattern := _glob(location)) is not None:
            tables[str(name)] = pattern
    return tables
