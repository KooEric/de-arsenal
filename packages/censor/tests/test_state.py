import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from _projects import bare_project
from _state import PIPELINE, snapshot, write_split_dlq, write_state

from arsenal_core.state.migrations import MIGRATIONS
from censor.context import load_context
from censor.state import read_pipeline_state

NOW = datetime(2026, 11, 25, 9, 30, 0, tzinfo=UTC)
RAN = NOW - timedelta(hours=2)


def _read(root: Path):  # noqa: ANN202 - test helper
    ctx = load_context(root)
    return read_pipeline_state(ctx.root, ctx.pipelines[0], NOW)


def test_a_pipeline_that_never_ran_has_no_state(tmp_path: Path) -> None:
    assert _read(bare_project(tmp_path)) is None


def test_reads_units_rows_rejects_and_last_completion(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    write_state(root, at=RAN, done=3, failed=1, quarantined=1)
    write_split_dlq(root, 4)
    state = _read(root)
    assert state is not None and not state.busy
    assert state.name == PIPELINE
    assert state.units == {"done": 3, "failed": 1, "quarantined": 1}
    assert state.rows_done == 30
    assert state.rejected_rows == 4
    assert state.last_completed_at == RAN
    assert state.lease_until is None


def test_reading_state_changes_nothing_next_to_the_state_db(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    write_state(root, at=RAN, done=2)
    before = snapshot(root)
    assert _read(root) is not None
    assert snapshot(root) == before
    assert not list((root / ".arsenal" / "censor" / "tmp").glob("*"))  # the copy is removed


def test_an_old_schema_state_db_is_read_without_being_migrated(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    db = root / ".arsenal" / f"{PIPELINE}.db"
    db.parent.mkdir()
    con = sqlite3.connect(db)
    for statement in MIGRATIONS[1]:
        con.execute(statement)
    con.execute("PRAGMA user_version=1")
    con.execute(
        "INSERT INTO units VALUES ('u', ?, 'k', '{}', 'done', 0, NULL, 5, 0, 0, ?, ?)",
        (PIPELINE, RAN.isoformat(), RAN.isoformat()),
    )
    con.commit()
    con.close()
    before = snapshot(root)
    state = _read(root)
    assert state is not None and state.units == {"done": 1} and state.rows_done == 5
    assert snapshot(root) == before
    assert not (root / ".arsenal" / "backups").exists()


def test_an_unexpired_lease_is_reported_and_an_expired_one_is_not(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    write_state(root, at=NOW, done=1, lease_seconds=600)
    state = _read(root)
    assert state is not None and state.lease_until == NOW + timedelta(seconds=600)
    ctx = load_context(root)
    later = read_pipeline_state(ctx.root, ctx.pipelines[0], NOW + timedelta(hours=1))
    assert later is not None and later.lease_until is None


def test_commits_still_in_the_wal_of_a_live_writer_are_seen(tmp_path: Path) -> None:
    from arsenal_core.state import StateStore, UnitSpec

    root = bare_project(tmp_path)
    db = write_state(root, at=RAN, done=1)
    writer = StateStore(db, clock=lambda: RAN)
    try:
        unit = UnitSpec.create(pipeline=PIPELINE, source="s", unit_key="live", payload={})
        writer.register(unit)
        state = _read(root)
    finally:
        writer.close()
    assert state is not None and state.units == {"done": 1, "pending": 1}


def test_a_corrupt_state_db_reads_as_busy(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    db = root / ".arsenal" / f"{PIPELINE}.db"
    db.parent.mkdir()
    db.write_bytes(b"not a database, long enough to be read as a header" * 4)
    state = _read(root)
    assert state is not None and state.busy


def test_state_dir_from_the_spec_is_resolved_against_the_project(tmp_path: Path) -> None:
    root = bare_project(tmp_path)
    collect = root / "collect.yaml"
    collect.write_text(collect.read_text() + "state_dir: ./var/state\n")
    write_state(root, at=RAN, done=1, state_dir="var/state")
    state = _read(root)
    assert state is not None and state.units == {"done": 1}
