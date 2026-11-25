"""Build Pugio state and DLQ records next to a test project, and snapshot a directory tree."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from arsenal_core.state import StateStore, UnitSpec

PIPELINE = "demo-collect"


def snapshot(root: Path) -> dict[str, tuple[str, int, int]]:
    """Every file outside `.arsenal/censor/`: content hash, size and mtime."""
    found: dict[str, tuple[str, int, int]] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts[:2] == (".arsenal", "censor") or not path.is_file():
            continue
        stat = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        found[str(rel)] = (digest, stat.st_size, stat.st_mtime_ns)
    return found


def write_state(
    root: Path,
    *,
    at: datetime,
    done: int = 0,
    failed: int = 0,
    quarantined: int = 0,
    rows_per_unit: int = 10,
    lease_seconds: float | None = None,
    name: str = PIPELINE,
    state_dir: str = ".arsenal",
) -> Path:
    db = root / state_dir / f"{name}.db"
    store = StateStore(db, clock=lambda: at)
    try:
        n = 0
        for status, count in (("done", done), ("failed", failed), ("quarantined", quarantined)):
            for _ in range(count):
                n += 1
                unit = UnitSpec.create(pipeline=name, source="s", unit_key=f"u{n}", payload={})
                store.register(unit)
                if status == "done":
                    store.mark_done(unit.unit_id, row_count=rows_per_unit)
                elif status == "failed":
                    store.mark_failed(unit.unit_id, "boom")
                else:
                    store.mark_quarantined(unit.unit_id, "bad rows")
        if lease_seconds is not None:
            assert store.acquire_lease(f"pipeline:{name}", "pugio-test", lease_seconds)
    finally:
        store.close()
    return db


def write_split_dlq(root: Path, rows: int, *, name: str = PIPELINE) -> None:
    folder = root / ".arsenal" / "dlq" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "u.json").write_text(json.dumps({"mode": "split", "rejected_rows": rows}))


def publish_dataset(root: Path, *, max_age_hours: float | None = None) -> Path:
    """Run `arsenal dataset apply` in `root` (the store path is relative to the cwd)."""
    import contextlib
    import os

    from arsenal.dataset import execute

    (root / "input").mkdir(exist_ok=True)
    (root / "input" / "a.csv").write_text("id,v\n1,a\n2,b\n")
    with contextlib.chdir(root):
        execute(True, "input/*.csv", None, "id", None, None, max_age_hours)
    assert os.path.exists(root / ".arsenal" / "dataset.duckdb")
    return root / ".arsenal" / "dataset.duckdb"


def fail_dataset(store: Path, *, drop_published: bool = False) -> None:
    import duckdb

    with duckdb.connect(str(store)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS failure (message VARCHAR, failed_at TIMESTAMPTZ)")
        db.execute("INSERT INTO failure VALUES ('input vanished', now())")
        if drop_published:
            db.execute("DROP TABLE IF EXISTS published")
