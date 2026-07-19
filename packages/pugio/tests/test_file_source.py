from pathlib import Path

import pytest

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import FileSourceSpec
from pugio.sources.file import FileSource


def test_each_file_is_one_unit_sorted(tmp_path: Path) -> None:
    for name in ["b.csv", "a.csv"]:
        (tmp_path / name).write_text("id,v\n1,x\n", encoding="utf-8")
    src = FileSource(FileSourceSpec(type="file", path=str(tmp_path / "*.csv")), pipeline="p")
    units = list(src.units())
    assert [u.unit_key for u in units] == ["a.csv", "b.csv"]  # 정렬 = 결정적 순서


def test_csv_fetch_returns_arrow(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_text("id,v\n1,x\n2,y\n", encoding="utf-8")
    src = FileSource(FileSourceSpec(type="file", path=str(tmp_path / "*.csv")), pipeline="p")
    result = src.fetch(next(iter(src.units())))
    assert result.batch is not None
    assert result.batch.num_rows == 2
    assert result.exhausted is False  # 종료는 generator 소진이 담당


def test_jsonl_fetch_returns_arrow(tmp_path: Path) -> None:
    (tmp_path / "a.jsonl").write_text('{"id": 1}\n{"id": 2}\n{"id": 3}\n', encoding="utf-8")
    spec = FileSourceSpec(type="file", path=str(tmp_path / "*.jsonl"))
    src = FileSource(spec, pipeline="p")
    result = src.fetch(next(iter(src.units())))
    assert result.batch is not None
    assert result.batch.num_rows == 3


def test_euc_kr_csv_decodes_with_explicit_encoding(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_bytes("id,v\n1,가\n".encode("euc-kr"))
    spec = FileSourceSpec(type="file", path=str(tmp_path / "a.csv"), encoding="euc-kr")
    src = FileSource(spec, pipeline="p")
    result = src.fetch(next(iter(src.units())))
    assert result.batch is not None
    assert result.batch.to_pylist()[0]["v"] == "가"


def test_explicit_format_overrides_extension(tmp_path: Path) -> None:
    (tmp_path / "a.dat").write_text('{"id": 1}\n', encoding="utf-8")
    spec = FileSourceSpec(type="file", path=str(tmp_path / "a.dat"), format="jsonl")
    src = FileSource(spec, pipeline="p")
    result = src.fetch(next(iter(src.units())))
    assert result.batch is not None
    assert result.batch.num_rows == 1


def test_unknown_extension_without_explicit_format_is_fatal(tmp_path: Path) -> None:
    (tmp_path / "a.dat").write_text("whatever", encoding="utf-8")
    spec = FileSourceSpec(type="file", path=str(tmp_path / "a.dat"))
    src = FileSource(spec, pipeline="p")
    with pytest.raises(FatalError, match="auto-detect"):
        src.fetch(next(iter(src.units())))


def test_unreadable_file_is_fatal(tmp_path: Path) -> None:
    (tmp_path / "bad.jsonl").write_text("{not valid json", encoding="utf-8")
    spec = FileSourceSpec(type="file", path=str(tmp_path / "bad.jsonl"))
    src = FileSource(spec, pipeline="p")
    with pytest.raises(FatalError, match="cannot parse"):
        src.fetch(next(iter(src.units())))


def test_excel_without_extra_is_fatal(tmp_path: Path) -> None:
    (tmp_path / "a.xlsx").write_bytes(b"not a real workbook")
    spec = FileSourceSpec(type="file", path=str(tmp_path / "a.xlsx"))
    src = FileSource(spec, pipeline="p")
    with pytest.raises(FatalError, match="excel"):
        src.fetch(next(iter(src.units())))
