"""StateStore 스키마 스냅샷 기록과 P1 드리프트 비교의 기반 테스트."""

import sqlite3
from pathlib import Path

from arsenal_core.state import StateStore


def make_store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "state.db")


def count_snapshots(db_path: Path, pipeline: str) -> int:
    """StateStore의 private 커넥션을 건드리지 않고, 별도 커넥션으로 행 수를 확인한다."""
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT count(*) FROM schema_snapshots WHERE pipeline=?", (pipeline,)
        ).fetchone()
        return int(row[0])
    finally:
        con.close()


def test_snapshot_appends_only_on_change(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store = StateStore(db_path)
    try:
        schema_v1 = '[{"name": "id", "type": "int64", "nullable": false}]'
        store.snapshot_schema("p", schema_v1)
        store.snapshot_schema("p", schema_v1)  # 동일 스키마 재기록 — no-op이어야 함
        assert count_snapshots(db_path, "p") == 1

        schema_v2 = (
            '[{"name": "id", "type": "int64", "nullable": false}, '
            '{"name": "v", "type": "string", "nullable": true}]'
        )
        store.snapshot_schema("p", schema_v2)  # 다른 스키마 — 새 행 추가
        assert count_snapshots(db_path, "p") == 2
    finally:
        store.close()


def test_last_schema_returns_latest(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        schema_v1 = '[{"name": "id", "type": "int64", "nullable": false}]'
        schema_v2 = (
            '[{"name": "id", "type": "int64", "nullable": false}, '
            '{"name": "v", "type": "string", "nullable": true}]'
        )
        store.snapshot_schema("p", schema_v1)
        store.snapshot_schema("p", schema_v2)
        assert store.last_schema("p") == schema_v2
    finally:
        store.close()


def test_last_schema_none_when_empty(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        assert store.last_schema("nonexistent-pipeline") is None
    finally:
        store.close()
