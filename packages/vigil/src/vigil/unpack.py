"""Turn CloudTrail log files into JSONL that Pugio's file source can read.

CloudTrail delivers `{"Records": [event, ...]}` files, usually gzipped. Each event becomes
one line `{"raw": "<the event as a JSON string>"}`: one string column, so events with
different nested shapes still load with a fixed schema. Field extraction happens later in SQL.
"""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
from typing import Any

from arsenal_core.errors import FatalError


def unpack_cloudtrail(src_dir: Path, out_file: Path) -> int:
    if not src_dir.is_dir():
        raise FatalError(f"source directory not found: {src_dir}")
    files = sorted([*src_dir.rglob("*.json"), *src_dir.rglob("*.json.gz")])
    if not files:
        raise FatalError(f"no CloudTrail files (*.json, *.json.gz) under {src_dir}")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    temp = out_file.with_name(f".{out_file.name}.tmp")
    count = 0
    try:
        with temp.open("w", encoding="utf-8") as handle:
            for file in files:
                for record in _records(file):
                    raw = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                    handle.write(json.dumps({"raw": raw}, ensure_ascii=False) + "\n")
                    count += 1
    except FatalError:
        temp.unlink(missing_ok=True)
        raise
    os.replace(temp, out_file)
    return count


def _records(file: Path) -> list[Any]:
    opener = gzip.open if file.suffix == ".gz" else open
    try:
        with opener(file, "rt", encoding="utf-8") as handle:
            document: Any = json.load(handle)
    except (OSError, ValueError) as e:
        raise FatalError(f"cannot read {file}: {e}") from e
    records: Any = document.get("Records") if isinstance(document, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(records, list):
        raise FatalError(f'{file}: not a CloudTrail log file (no "Records" array)')
    return records  # pyright: ignore[reportUnknownVariableType]
