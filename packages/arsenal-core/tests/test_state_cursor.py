from pathlib import Path

from arsenal_core.state import StateStore


def test_cursor_roundtrip_and_upsert(tmp_path: Path) -> None:
    """B5: get_cursor는 없으면 None, set_cursor는 upsert (같은 키 재설정 시 갱신)."""
    store = StateStore(tmp_path / "state.db")
    assert store.get_cursor("p", "https://api.test/items") is None
    store.set_cursor("p", "https://api.test/items", "abc")
    assert store.get_cursor("p", "https://api.test/items") == "abc"
    store.set_cursor("p", "https://api.test/items", "def")
    assert store.get_cursor("p", "https://api.test/items") == "def"
