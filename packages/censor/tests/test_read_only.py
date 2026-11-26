"""Censor never writes to what it audits: content hash, size and mtime of every file outside
`.arsenal/censor/` are the same before and after a Standard audit."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from _data import DUPLICATE, ROWS, write_duckdb, write_parquet
from _projects import production_project
from _state import PIPELINE, publish_dataset, snapshot, write_split_dlq, write_state
from typer.testing import CliRunner

from arsenal_core.state import StateStore, UnitSpec
from arsenal_core.state.migrations import MIGRATIONS
from censor.cli import app

runner = CliRunner()
RAN = datetime.now(UTC) - timedelta(hours=3)


def _dirs(root: Path) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_dir() and p.relative_to(root).parts[:2] != (".arsenal", "censor")
    )


def _old_schema_state(root: Path, name: str) -> None:
    con = sqlite3.connect(root / ".arsenal" / f"{name}.db")
    for statement in MIGRATIONS[1]:
        con.execute(statement)
    con.execute("PRAGMA user_version=1")
    con.commit()
    con.close()


def test_standard_project_audit_changes_nothing_outside_censor(tmp_path: Path) -> None:
    root = production_project(tmp_path)
    db = write_state(root, at=RAN, done=3, failed=1, quarantined=1)
    write_split_dlq(root, 2)
    _old_schema_state(root, "legacy")  # another pipeline's state, never migrated by Censor
    publish_dataset(root, max_age_hours=24)
    write_parquet(root / "data" / "raw" / "u1.parquet", [*ROWS, DUPLICATE])
    write_parquet(root / "data" / "clean" / "part.parquet", ROWS)
    writer = StateStore(db, clock=lambda: RAN)  # a run in progress: -wal and -shm are live
    try:
        writer.register(UnitSpec.create(pipeline=PIPELINE, source="s", unit_key="x", payload={}))
        files, dirs = snapshot(root), _dirs(root)
        assert f".arsenal/{PIPELINE}.db-wal" in files
        result = runner.invoke(app, ["audit", str(root), "--tier", "standard", "--json-only"])
        assert snapshot(root) == files
        assert _dirs(root) == dirs
    finally:
        writer.close()
    assert result.exit_code == 1  # the failed units are reported
    assert '"reliability.failed_units:collect.yaml"' in result.stdout
    censor = root / ".arsenal" / "censor"
    assert sorted(p.name for p in censor.iterdir()) == [".gitignore", "reports", "runs", "tmp"]
    assert not list((censor / "tmp").iterdir())


def test_files_and_duckdb_audits_change_nothing(tmp_path: Path) -> None:
    data = tmp_path / "data"
    write_parquet(data / "a.parquet", [*ROWS, DUPLICATE])
    (data / "b.csv").write_text("id,v\n1,a\n")
    store = write_duckdb(data / "x.duckdb", "published", 5000, duplicate_every=10)
    base = tmp_path / "base"
    base.mkdir()
    before, listing = snapshot(data), sorted(p.name for p in data.iterdir())
    runs = [
        ["--files", str(data / "*.parquet"), "--key", "id", "--time-column", "ts"],
        ["--files", str(data / "*.csv"), "--key", "id", "--sample", "50%"],
        ["--duckdb", str(store), "--table", "published", "--key", "id", "--full"],
        ["--duckdb", str(store), "--table", "published", "--sample", "10", "--time-column", "ts"],
    ]
    for args in runs:
        result = runner.invoke(app, ["audit", str(base), *args, "--json-only"])
        assert result.exit_code in (0, 1), result.output
        assert snapshot(data) == before
        assert sorted(p.name for p in data.iterdir()) == listing  # no .wal, no temp file
    assert [p.name for p in base.iterdir()] == [".arsenal"]
    assert [p.name for p in (base / ".arsenal").iterdir()] == ["censor"]
