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


def test_mark_done_and_advance_cursor_atomic(tmp_path: Path) -> None:
    """M2 최종 리뷰 FIX 1: done 마킹과 커서 전진이 한 커밋으로 묶여야 한다 —
    별개 트랜잭션이면 그 사이 크래시 시 unit은 done인데 커서는 스테일 상태로
    남아 재실행이 무한루프에 빠진다 (runner.py에서 상세 설명)."""
    store = StateStore(tmp_path / "state.db")
    u = make_unit("after=c1")
    store.register(u)
    store.mark_done_and_advance_cursor(
        u.unit_id,
        pipeline="p",
        source="s",
        cursor="c2",
        row_count=10,
        byte_count=100,
        duration_ms=5,
    )
    assert store.is_done(u.unit_id) is True
    assert store.get_cursor("p", "s") == "c2"
    m = store.metrics(u.unit_id)
    assert (m.row_count, m.byte_count, m.duration_ms) == (10, 100, 5)


def test_mark_done_and_advance_cursor_survives_reopen(tmp_path: Path) -> None:
    """두 UPDATE/INSERT가 진짜 하나의 커밋인지 — 재오픈(프로세스 재시작 시뮬레이션)
    후에도 둘 다(또는 둘 다 아님)만 관측되어야 한다."""
    db = tmp_path / "state.db"
    store = StateStore(db)
    u = make_unit("after=c1")
    store.register(u)
    store.mark_done_and_advance_cursor(u.unit_id, pipeline="p", source="s", cursor="c2")
    store.close()

    reopened = StateStore(db)
    assert reopened.is_done(u.unit_id) is True
    assert reopened.get_cursor("p", "s") == "c2"
