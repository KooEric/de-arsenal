"""AQ-0 DoD: `pip install de-aquila` pulls in no other weapon (pull model, AQ-ADR-1)."""

from __future__ import annotations

import re
from importlib import metadata

OTHER_WEAPONS = {
    "pugio",
    "de-gladius",
    "de-hasta",
    "de-ballista",
    "de-augur",
    "de-onager",
    "spatha",
    "de-scutum",
    "de-arsenal",
    "duckdb",
    "pyspark",
}


def _closure(root: str) -> set[str]:
    seen: set[str] = set()
    pending = [root]
    while pending:
        name = re.sub(r"[-_.]+", "-", pending.pop()).lower()
        if name in seen:
            continue
        seen.add(name)
        try:
            requires = metadata.requires(name) or []
        except metadata.PackageNotFoundError:
            continue
        for requirement in requires:
            if "extra" in requirement.partition(";")[2]:
                continue
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
            if match:
                pending.append(match.group(1))
    return seen


def test_de_aquila_depends_only_on_core_scorpio_and_readers() -> None:
    closure = _closure("de-aquila")

    assert {"arsenal-core", "de-scorpio", "pyarrow"} <= closure
    assert closure & OTHER_WEAPONS == set()


def test_no_weapon_depends_on_aquila() -> None:
    for dist in ("pugio", "de-gladius", "de-hasta", "de-ballista", "de-augur", "de-arsenal"):
        assert "de-aquila" not in _closure(dist), dist
