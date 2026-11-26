"""Scale guard (spec §6): measure before reading, and say what to do when it is too much."""

from __future__ import annotations

import glob
from dataclasses import dataclass
from pathlib import Path
from typing import Final

MAX_FILES: Final = 2_000
MAX_BYTES: Final = 5 * 1024**3
MAX_ESTIMATED_ROWS: Final = 50_000_000
_HIDDEN_PREFIXES: Final = (".", "_")  # _SUCCESS, .crc and the like are not data


@dataclass(frozen=True)
class Scale:
    files: int | None = None
    bytes: int | None = None
    estimated_rows: int | None = None
    unknown: bool = False  # a view or a partitioned parent: the catalog has no size for it


def list_files(pattern: str) -> list[Path]:
    paths = (Path(name) for name in sorted(glob.glob(pattern, recursive=True)))
    return [p for p in paths if p.is_file() and not p.name.startswith(_HIDDEN_PREFIXES)]


def measure_files(paths: list[Path]) -> Scale:
    """File count and total size from `stat`; no file is opened."""
    return Scale(files=len(paths), bytes=sum(p.stat().st_size for p in paths))


def exceeds(scale: Scale) -> bool:
    return (
        scale.unknown  # fail closed: an unmeasured target is read only when the user narrows it
        or (scale.files is not None and scale.files > MAX_FILES)
        or (scale.bytes is not None and scale.bytes > MAX_BYTES)
        or (scale.estimated_rows is not None and scale.estimated_rows > MAX_ESTIMATED_ROWS)
    )


def _count(value: int) -> str:
    for limit, unit in ((1_000_000_000, "B"), (1_000_000, "M")):
        if value >= limit:
            return f"{value / limit:.3g}{unit}"
    return f"{value:,}"


def describe(scale: Scale) -> str:
    """`18,432 files, 41.2 GB (estimated 620M rows)`, with only the parts that were measured."""
    if scale.unknown:
        return "size unknown (a view or a partitioned table has no size in the catalog)"
    parts: list[str] = []
    if scale.files is not None:
        parts.append(f"{scale.files:,} files")
    if scale.bytes is not None:
        parts.append(f"{scale.bytes / 1024**3:.1f} GB")
    text = ", ".join(parts)
    if scale.estimated_rows is not None:
        rows = f"estimated {_count(scale.estimated_rows)} rows"
        text = f"{text} ({rows})" if text else rows
    return text


def guidance(label: str, scale: Scale, *, is_files: bool, since_example: str) -> str:
    lines = [
        f"{label}: {describe(scale)}.",
        "Profiling everything would take a long time and a lot of memory, so it was skipped.",
        "Pick one:",
        '  --sample 1%                 profile a sample only (key uniqueness is reported as "found'
        ' in the sample")',
        f"  --since {since_example}          only this period (needs --time-column)",
    ]
    if is_files:
        lines.append("  --files '<narrower glob>'   narrow to recent partitions")
    lines += [
        "  --full                      read everything (memory limit 4GB, spills to disk)",
        "Quick and the other dimensions were evaluated as usual.",
    ]
    return "\n".join(lines)
