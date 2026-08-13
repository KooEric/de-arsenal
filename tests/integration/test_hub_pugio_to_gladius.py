"""Pugio → Gladius 허브 통합 테스트 (M3 Task 3.8).

증명 대상: Arrow/Parquet 허브 계약 — Pugio가 쓴 Parquet을 Gladius가 그대로
읽어 스키마와 행 수를 보존한 채 변환할 수 있다 (두 패키지가 파일 포맷으로만
결합되고, 서로의 내부를 몰라도 된다는 아키텍처 원칙의 실물 증거).

흐름:
  CSV(2개 파일, 파티션 흉내) → run_pipeline(file source → parquet sink)
    → run_transform(filter+cast+derive+select) → query()로 실제 값 검증.
"""

import csv
from pathlib import Path

import pytest

from arsenal_core.spec.models import FileSourceSpec, ParquetSinkSpec, PipelineSpec
from gladius.engine import query, run_transform
from gladius.spec import TransformSpec
from pugio.runner import run_pipeline


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "name", "amount", "state"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


@pytest.fixture
def raw_parquet_dir(tmp_path: Path) -> Path:
    """두 개의 CSV 파일을 file source로 수집해 Parquet 허브(raw/)를 만든다.

    파일 두 개(=unit 두 개)로 나눠 트랜스파일러의 `read_parquet(..., union_by_name=true)`
    글롭이 여러 파티션을 하나로 합치는 경로도 함께 검증한다.
    """
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    _write_csv(
        input_dir / "batch1.csv",
        [
            {"id": "1", "name": "Alice", "amount": "10.50", "state": "open"},
            {"id": "2", "name": "Bob", "amount": "20.00", "state": "closed"},
        ],
    )
    _write_csv(
        input_dir / "batch2.csv",
        [
            {"id": "3", "name": "Carol", "amount": "5.25", "state": "open"},
            {"id": "4", "name": "Dave", "amount": "99.99", "state": "closed"},
        ],
    )

    pipeline_spec = PipelineSpec.model_validate(
        {
            "name": "hub-test",
            "state_dir": tmp_path / ".arsenal",
            "source": {
                "type": "file",
                "path": str(input_dir / "*.csv"),
                "format": "csv",
            },
            "sink": {"type": "parquet", "path": str(tmp_path / "data" / "raw")},
        }
    )
    assert isinstance(pipeline_spec.source, FileSourceSpec)
    assert isinstance(pipeline_spec.sink, ParquetSinkSpec)

    report = run_pipeline(pipeline_spec)
    assert report.written == 2  # 파일 두 개 = unit 두 개, 각각 sink에 씀
    assert report.skipped == 0

    raw_dir = tmp_path / "data" / "raw"
    assert raw_dir.exists()
    assert list(raw_dir.glob("*.parquet"))
    return raw_dir


def test_gladius_reads_pugio_parquet_output_directly(raw_parquet_dir: Path, tmp_path: Path) -> None:
    """Pugio가 쓴 raw/ 디렉터리를 gladius TransformSpec.input에 그대로 넣어 변환한다.

    filter(state='open') + cast(amount→double) + derive(신규 컬럼) + select로
    허브를 오가는 동안 값과 파생 계산이 정확히 보존되는지 확인한다 — smoke가
    아니라 실제 숫자 비교.
    """
    clean_dir = tmp_path / "data" / "clean"
    transform_spec = TransformSpec.model_validate(
        {
            "name": "hub-clean",
            "input": str(raw_parquet_dir),
            "steps": [
                {"filter": "state = 'open'"},
                {"cast": {"amount": "double"}},
                {"derive": {"amount_with_tax": "amount * 1.1"}},
                {"select": ["id", "name", "amount", "amount_with_tax", "state"]},
            ],
            "output": str(clean_dir),
        }
    )

    out = run_transform(transform_spec)

    assert out == clean_dir
    assert list(out.glob("*.parquet"))

    result = query(f"SELECT * FROM '{out}/*.parquet' ORDER BY id")

    # state='open' 필터를 통과하는 건 id 1, 3 뿐 — 4개 입력 행 중 2개.
    assert result.num_rows == 2
    assert result.column("id").to_pylist() == [1, 3]
    assert result.column("name").to_pylist() == ["Alice", "Carol"]
    assert result.column("state").to_pylist() == ["open", "open"]

    amounts = result.column("amount").to_pylist()
    assert amounts == pytest.approx([10.50, 5.25])
    # derive 단계가 만든 파생 컬럼 — 원본 값과의 관계로 검증 (하드코딩된 값이
    # 아니라 실제 계산식이 보존됐는지 확인).
    tax = result.column("amount_with_tax").to_pylist()
    assert tax == pytest.approx([a * 1.1 for a in amounts])

    # 스키마 보존: select에 명시한 컬럼 순서/이름이 그대로.
    assert result.schema.names == ["id", "name", "amount", "amount_with_tax", "state"]
    assert str(result.schema.field("amount").type) == "double"


def test_row_count_preserved_end_to_end_without_filter(
    raw_parquet_dir: Path, tmp_path: Path
) -> None:
    """필터 없이 select만 적용하면 Pugio가 쓴 4개 행이 전부 Gladius를 통과한다.

    허브 계약의 핵심: 행 수 손실이 없어야 한다 (파티션 두 개가 하나로 합쳐짐).
    """
    clean_dir = tmp_path / "data" / "clean_all"
    transform_spec = TransformSpec.model_validate(
        {
            "name": "hub-passthrough",
            "input": str(raw_parquet_dir),
            "steps": [{"select": ["id", "name", "amount", "state"]}],
            "output": str(clean_dir),
        }
    )

    out = run_transform(transform_spec)
    result = query(f"SELECT * FROM '{out}/*.parquet' ORDER BY id")

    assert result.num_rows == 4
    assert result.column("id").to_pylist() == [1, 2, 3, 4]
