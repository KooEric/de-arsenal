import gzip
import json
from pathlib import Path

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
