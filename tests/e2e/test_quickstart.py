"""E2E: examples/quickstart의 collect→transform 흐름을 그대로 재현한다 (M4 Task 4.2).

네트워크·Docker 없이 오프라인으로 끝난다 — 번들된 orders.csv를 file source로
읽는다. README와 동일한 흐름을 프로그램적으로 검증해 문서 드리프트를 잡는다.
"""

import shutil
from pathlib import Path

import pytest

from arsenal_core.spec import load_pipeline
from gladius.engine import query, run_transform
from gladius.loader import load_transform
from pugio.runner import run_pipeline

QUICKSTART_SRC = Path(__file__).resolve().parents[2] / "examples" / "quickstart"


@pytest.fixture
def quickstart_project(tmp_path: Path) -> Path:
    """레포의 examples/quickstart를 tmp로 복사해 원본을 건드리지 않고 실행한다."""
    dest = tmp_path / "quickstart"
    shutil.copytree(QUICKSTART_SRC, dest)
    return dest


def test_quickstart_collect_then_transform_produces_expected_rows(
    quickstart_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(quickstart_project)

    pipeline_spec = load_pipeline(quickstart_project / "collect.yaml")
    collect_report = run_pipeline(pipeline_spec)
    assert collect_report.written == 1  # orders.csv는 파일 하나 = unit 하나

    transform_spec = load_transform(quickstart_project / "transform.yaml")
    out = run_transform(transform_spec)
    assert out.resolve() == quickstart_project / "data" / "orders_clean"
    assert list(out.glob("*.parquet"))

    result = query(f"SELECT * FROM '{out}/*.parquet' ORDER BY order_id")
    assert result.num_rows == 3  # 5개 주문 중 paid 3건만 남는다
    assert result.column("status").to_pylist() == ["paid", "paid", "paid"]
    assert result.column("order_id").to_pylist() == [1, 3, 5]
    assert sum(result.column("amount").to_pylist()) == pytest.approx(262.49)


def test_quickstart_readme_matches_shipped_specs(quickstart_project: Path) -> None:
    """README의 복붙 명령이 참조하는 파일이 실제로 존재하는지 확인 (문서 드리프트 방지)."""
    readme = (quickstart_project / "README.md").read_text(encoding="utf-8")
    assert "pugio run collect.yaml" in readme
    assert "gladius run transform.yaml" in readme
    assert "arsenal query" in readme
    assert (quickstart_project / "collect.yaml").exists()
    assert (quickstart_project / "transform.yaml").exists()
    assert (quickstart_project / "orders.csv").exists()
