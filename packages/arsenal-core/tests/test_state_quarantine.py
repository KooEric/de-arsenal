from pathlib import Path

from arsenal_core.state import StateStore, UnitSpec


def make_unit(key: str) -> UnitSpec:
    return UnitSpec.create(pipeline="p", source="s", unit_key=key, payload={"offset": 0})


def test_mark_quarantined_sets_status_and_reason(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    u = make_unit("offset=0")
    store.register(u)
    store.mark_quarantined(u.unit_id, "not_null violation on v")
    rec = store.get(u.unit_id)
    assert rec.status == "quarantined"
    assert rec.last_error == "not_null violation on v"


def test_quarantined_lists_only_quarantined_units(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    u1, u2, u3 = make_unit("a"), make_unit("b"), make_unit("c")
    store.register(u1)
    store.register(u2)
    store.register(u3)
    store.mark_quarantined(u1.unit_id, "bad")
    store.mark_done(u2.unit_id)
    quarantined = store.quarantined("p")
    assert [r.unit_id for r in quarantined] == [u1.unit_id]


def test_quarantined_is_not_done(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    u = make_unit("offset=0")
    store.register(u)
    store.mark_quarantined(u.unit_id, "bad")
    assert store.is_done(u.unit_id) is False


def test_mark_quarantined_does_not_bump_attempts(tmp_path: Path) -> None:
    """quarantine은 실패 재시도가 아니다 — attempts를 올리면 재시도 카운트가 오염된다."""
    store = StateStore(tmp_path / "state.db")
    u = make_unit("offset=0")
    store.register(u)
    store.mark_quarantined(u.unit_id, "bad")
    rec = store.get(u.unit_id)
    assert rec.attempts == 0
