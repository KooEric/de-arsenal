from pathlib import Path

import pyarrow as pa
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


def test_csv_na_token_is_data_even_when_every_value_is_na(tmp_path: Path) -> None:
    """회귀: 컬럼 값이 전부 "NA"(북미·나미비아)인 파일도 값이 보존돼야 한다.

    pyarrow 기본 null 토큰을 쓰면 이 파일의 continent가 null 타입으로 추론돼
    통째로 사라졌다 — 같은 데이터를 대륙별로 나눠 넣었을 때 46%의 행이 손상됐다.
    """
    (tmp_path / "na.csv").write_text("id,continent\n1,NA\n2,NA\n", encoding="utf-8")
    (tmp_path / "mixed.csv").write_text("id,continent\n3,NA\n4,EU\n", encoding="utf-8")
    src = FileSource(FileSourceSpec(type="file", path=str(tmp_path / "*.csv")), pipeline="p")
    by_file = {u.unit_key: src.fetch(u).batch for u in src.units()}
    for key, expected in {"na.csv": ["NA", "NA"], "mixed.csv": ["NA", "EU"]}.items():
        batch = by_file[key]
        assert batch is not None
        assert batch.schema.field("continent").type == pa.string()
        assert batch.column("continent").to_pylist() == expected


def test_csv_empty_cell_is_still_null_for_numbers(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_text("id,elevation\n1,10\n2,\n", encoding="utf-8")
    src = FileSource(FileSourceSpec(type="file", path=str(tmp_path / "a.csv")), pipeline="p")
    batch = src.fetch(next(iter(src.units()))).batch
    assert batch is not None
    assert batch.column("elevation").to_pylist() == [10, None]


def test_csv_null_values_are_configurable(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_text("id,score\n1,N/A\n2,7\n", encoding="utf-8")
    spec = FileSourceSpec(type="file", path=str(tmp_path / "a.csv"), null_values=["", "N/A"])
    src = FileSource(spec, pipeline="p")
    batch = src.fetch(next(iter(src.units()))).batch
    assert batch is not None
    assert batch.column("score").to_pylist() == [None, 7]


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


def _fetch_one(tmp_path: Path, text: str, **spec: object) -> pa.RecordBatch:
    (tmp_path / "a.csv").write_text(text, encoding="utf-8")
    source = FileSource(
        FileSourceSpec.model_validate({"type": "file", "path": str(tmp_path / "a.csv"), **spec}),
        pipeline="p",
    )
    batch = source.fetch(next(iter(source.units()))).batch
    assert batch is not None
    return batch


def test_leading_zeros_survive(tmp_path: Path) -> None:
    """ "01234" read as a number becomes 1234: a zip code silently changed."""
    batch = _fetch_one(tmp_path, "id,zip,qty,neg\n1,01234,0,-007\n2,94105,10,5\n")

    assert batch.column("zip").to_pylist() == ["01234", "94105"]
    assert batch.column("neg").to_pylist() == ["-007", "5"]
    assert batch.schema.field("id").type == pa.int64()
    assert batch.column("qty").to_pylist() == [0, 10]  # a lone 0 is a number


def test_column_types_pin_a_type(tmp_path: Path) -> None:
    batch = _fetch_one(
        tmp_path,
        "id,zip,amount,day\n1,94105,3,2026-01-02\n",
        column_types={"zip": "string", "amount": "float64", "id": "string", "day": "date"},
    )

    assert batch.to_pylist() == [
        {"id": "1", "zip": "94105", "amount": 3.0, "day": __import__("datetime").date(2026, 1, 2)}
    ]


def test_column_types_can_force_a_number_despite_leading_zeros(tmp_path: Path) -> None:
    batch = _fetch_one(tmp_path, "seq\n007\n", column_types={"seq": "int64"})
    assert batch.column("seq").to_pylist() == [7]


def test_column_types_typo_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match=r"does not have: \['zipp'\]"):
        _fetch_one(tmp_path, "id,zip\n1,2\n", column_types={"zipp": "string"})


def test_column_types_reject_unparseable_values(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="cannot parse"):
        _fetch_one(tmp_path, "n\nabc\n", column_types={"n": "int64"})


def test_column_types_are_csv_only(tmp_path: Path) -> None:
    (tmp_path / "a.jsonl").write_text('{"id": 1}\n', encoding="utf-8")
    spec = FileSourceSpec.model_validate(
        {"type": "file", "path": str(tmp_path / "a.jsonl"), "column_types": {"id": "string"}}
    )
    source = FileSource(spec, pipeline="p")
    with pytest.raises(FatalError, match="csv files only"):
        source.fetch(next(iter(source.units())))


def test_a_path_matching_nothing_is_an_error_unless_allowed(tmp_path: Path) -> None:
    pattern = str(tmp_path / "missing" / "*.csv")
    with pytest.raises(FatalError, match="matches no files"):
        list(FileSource(FileSourceSpec(type="file", path=pattern), pipeline="p").units())

    allowed = FileSourceSpec(type="file", path=pattern, allow_empty=True)
    assert list(FileSource(allowed, pipeline="p").units()) == []


def test_detect_changes_reingests_an_overwritten_file(tmp_path: Path) -> None:
    def unit_keys(detect: bool) -> list[str]:
        spec = FileSourceSpec(type="file", path=str(tmp_path / "*.csv"), detect_changes=detect)
        return [unit.unit_key for unit in FileSource(spec, pipeline="p").units()]

    export = tmp_path / "export.csv"
    export.write_text("id,paid\n1,false\n", encoding="utf-8")
    plain, tracked = unit_keys(False), unit_keys(True)

    assert unit_keys(True) == tracked  # same content, same unit: skipped on a rerun
    export.write_text("id,paid\n1,true\n", encoding="utf-8")
    assert unit_keys(False) == plain == ["export.csv"]  # default: the edit is invisible
    assert unit_keys(True) != tracked and unit_keys(True)[0].startswith("export.csv@")


def test_detect_changes_is_refused_with_a_parquet_sink(tmp_path: Path) -> None:
    from pydantic import ValidationError

    from arsenal_core.spec.models import PipelineSpec

    raw: dict[str, object] = {
        "name": "p",
        "source": {"type": "file", "path": str(tmp_path / "*.csv"), "detect_changes": True},
        "sink": {"type": "parquet", "path": str(tmp_path / "out")},
    }
    with pytest.raises(ValidationError, match="would then hold the old and the new rows"):
        PipelineSpec.model_validate(raw)
    raw["sink"] = {
        "type": "duckdb",
        "path": str(tmp_path / "o.duckdb"),
        "table": "t",
        "merge_key": ["id"],
    }
    assert PipelineSpec.model_validate(raw).source.type == "file"


@pytest.mark.parametrize("name", ["a.xlsb", "a.XLSB", "a.xlsm"])
def test_binary_and_macro_workbooks_auto_detect_as_excel(tmp_path: Path, name: str) -> None:
    """Ledgers often come as .xlsb or .xlsm. fastexcel (calamine) reads both, so the
    suffix must reach the excel reader instead of failing format detection."""
    (tmp_path / name).write_bytes(b"not a real workbook")
    spec = FileSourceSpec(type="file", path=str(tmp_path / name))
    src = FileSource(spec, pipeline="p")
    with pytest.raises(FatalError) as exc_info:
        src.fetch(next(iter(src.units())))
    assert "cannot auto-detect format" not in str(exc_info.value)


def test_csv_empty_cell_is_null_for_strings(tmp_path: Path) -> None:
    """회귀: 문자열 열의 빈 칸이 ''로 적재돼 not_null 검사와 censor NULL 검사를 통과했다."""
    batch = _fetch_one(tmp_path, "id,name\nA1,kim\n,\nA3,lee\n")

    assert batch.column("id").to_pylist() == ["A1", None, "A3"]
    assert batch.column("name").to_pylist() == ["kim", None, "lee"]


def test_csv_empty_cell_is_null_for_pinned_and_leading_zero_strings(tmp_path: Path) -> None:
    batch = _fetch_one(tmp_path, "id,zip\n1,01234\n,\n", column_types={"id": "string"})

    assert batch.column("id").to_pylist() == ["1", None]
    assert batch.column("zip").to_pylist() == ["01234", None]


def test_csv_custom_null_token_applies_to_strings(tmp_path: Path) -> None:
    batch = _fetch_one(tmp_path, "id,city\n1,N/A\n2,Seoul\n", null_values=["", "N/A"])

    assert batch.column("city").to_pylist() == [None, "Seoul"]


def test_csv_quoted_empty_string_is_also_null(tmp_path: Path) -> None:
    batch = _fetch_one(tmp_path, 'id,name\n1,""\n2,kim\n')

    assert batch.column("name").to_pylist() == [None, "kim"]
