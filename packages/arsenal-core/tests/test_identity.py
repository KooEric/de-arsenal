import re

from hypothesis import given
from hypothesis import strategies as st

from arsenal_core.identity import unit_id

TEXT = st.text(min_size=1, max_size=50)


@given(TEXT, TEXT, TEXT)
def test_deterministic(p: str, s: str, k: str) -> None:
    assert unit_id(p, s, k) == unit_id(p, s, k)


@given(TEXT, TEXT, TEXT, TEXT)
def test_different_key_different_id(p: str, s: str, k1: str, k2: str) -> None:
    if k1 != k2:
        assert unit_id(p, s, k1) != unit_id(p, s, k2)


def test_format_16_hex() -> None:
    assert re.fullmatch(r"[0-9a-f]{16}", unit_id("pipe", "src", "offset=0"))
