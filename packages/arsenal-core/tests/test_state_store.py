from pathlib import Path

from arsenal_core.state import StateStore, UnitSpec


def make_unit(key: str) -> UnitSpec:
    return UnitSpec.create(pipeline="p", source="s", unit_key=key, payload={"offset": 0})


def test_register_is_idempotent(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    u = make_unit("offset=0")
    store.register(u)
    store.register(u)  # 두 번 등록해도
    assert store.status(u.unit_id) == "pending"
    assert store.counts("p") == {"pending": 1}


def test_mark_done_survives_reopen(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    store = StateStore(db)
    u = make_unit("offset=0")
    store.register(u)
    store.mark_done(u.unit_id)
    store.close()
    reopened = StateStore(db)  # 프로세스 재시작 시뮬레이션
    assert reopened.status(u.unit_id) == "done"


def test_done_units_are_skippable(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    u1, u2 = make_unit("offset=0"), make_unit("offset=100")
    store.register(u1)
    store.register(u2)
    store.mark_done(u1.unit_id)
    assert store.is_done(u1.unit_id) is True
    assert store.is_done(u2.unit_id) is False


def test_mark_failed_records_error_and_attempts(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    u = make_unit("offset=0")
    store.register(u)
    store.mark_failed(u.unit_id, "boom")
    store.mark_failed(u.unit_id, "boom again")
    rec = store.get(u.unit_id)
    assert rec.status == "failed"
    assert rec.attempts == 2
    assert rec.last_error == "boom again"


def test_mark_done_records_unit_metrics(tmp_path: Path) -> None:
    """단위 지표는 비용 가시성의 원료 — P0부터 기록한다 (docs/07 참조)."""
    store = StateStore(tmp_path / "state.db")
    u = make_unit("offset=0")
    store.register(u)
    store.mark_done(u.unit_id, row_count=100, byte_count=2048, duration_ms=350)
    m = store.metrics(u.unit_id)
    assert (m.row_count, m.byte_count, m.duration_ms) == (100, 2048, 350)
