import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from arsenal_core.errors import FatalError
from vigil.unpack import unpack_cloudtrail


def _records(*names: str) -> str:
    return json.dumps({"Records": [{"eventID": name, "eventName": "X"} for name in names]})


def test_unpack_reads_json_and_gzip_into_raw_lines(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "nested").mkdir(parents=True)
    (src / "a.json").write_text(_records("e1", "e2"), encoding="utf-8")
    with gzip.open(src / "nested" / "b.json.gz", "wt", encoding="utf-8") as handle:
        handle.write(_records("e3"))
    out = tmp_path / "out" / "events.jsonl"

    assert unpack_cloudtrail(src, out) == 3

    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [list(line) for line in lines] == [["raw"], ["raw"], ["raw"]]
    assert [json.loads(line["raw"])["eventID"] for line in lines] == ["e1", "e2", "e3"]


def test_file_without_records_is_fatal_and_writes_nothing(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    bad = src / "bad.json"
    bad.write_text('{"events": []}', encoding="utf-8")
    out = tmp_path / "events.jsonl"
    with pytest.raises(FatalError, match="bad.json"):
        unpack_cloudtrail(src, out)
    assert not out.exists()


def test_missing_or_empty_source_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="not found"):
        unpack_cloudtrail(tmp_path / "nope", tmp_path / "o.jsonl")
    (tmp_path / "empty").mkdir()
    with pytest.raises(FatalError, match="no CloudTrail files"):
        unpack_cloudtrail(tmp_path / "empty", tmp_path / "o.jsonl")


def test_truncated_gzip_is_fatal_and_writes_nothing(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    full = gzip.compress(_records(*[f"e{i}" for i in range(200)]).encode())
    (src / "cut.json.gz").write_bytes(full[: len(full) // 2])
    out = tmp_path / "events.jsonl"
    with pytest.raises(FatalError, match="cannot read .*cut.json.gz"):
        unpack_cloudtrail(src, out)
    assert not out.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_corrupt_gzip_is_fatal(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    full = bytearray(gzip.compress(_records(*[f"e{i}" for i in range(200)]).encode()))
    full[20:40] = b"\xff" * 20
    (src / "bad.json.gz").write_bytes(bytes(full))
    with pytest.raises(FatalError, match="cannot read .*bad.json.gz"):
        unpack_cloudtrail(src, tmp_path / "events.jsonl")


def test_write_failure_is_fatal_and_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.json").write_text(_records("e1"), encoding="utf-8")
    out = tmp_path / "events.jsonl"

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("vigil.unpack.os.replace", boom)
    with pytest.raises(FatalError, match="cannot write .*disk full"):
        unpack_cloudtrail(src, out)
    assert not out.exists()
    assert list(tmp_path.glob(".*.tmp")) == []
