from pathlib import Path

import duckdb
import pytest

from augur.catalog import Catalog
from augur.evaluate import EvalCase, load_cases, rows_equal, run_eval, summarize
from augur.llm import FakeProvider

CASES = Path(__file__).resolve().parents[1] / "eval" / "cases.yaml"


def test_rows_equal_ignores_order_and_decimal() -> None:
    from decimal import Decimal

    assert rows_equal([(1, Decimal("2.5")), (2, 3.0)], [(2, 3.0), (1, 2.5)])
    assert not rows_equal([(1,)], [(2,)])


def test_cases_file_golden_sql_all_execute(catalog: Catalog, data_dir: Path) -> None:
    cases = load_cases(CASES)
    assert len(cases) >= 30
    for c in cases:
        sql = c.golden_sql.replace("./data", str(data_dir))
        duckdb.sql(sql).fetchall()
        assert all(catalog.get(tb) is not None for tb in c.tables)


def _rewrite(c: EvalCase, data_dir: Path) -> EvalCase:
    return c.model_copy(update={"golden_sql": c.golden_sql.replace("./data", str(data_dir))})


@pytest.mark.parametrize(
    ("case_id", "fake_sql", "expected"),
    [
        ("l1-001", "SELECT count(*) FROM './data/orders/*.parquet'", "CORRECT"),
        (
            "l1-001",
            "SELECT count(*) FROM './data/orders/*.parquet' WHERE status='paid'",
            "WRONG_RESULT",
        ),
        (
            "l1-001",
            "SELECT status, count(*) FROM './data/orders/*.parquet' GROUP BY 1",
            "WRONG_SHAPE",
        ),
        ("l1-001", "SELECT shipping FROM './data/orders/*.parquet'", "HALLUCINATED_COLUMN"),
        ("l1-001", "SELECT count(*) FROM './data/shipments/*.parquet'", "HALLUCINATED_TABLE"),
        ("l1-001", "SELEC count(*) FROM './data/orders/*.parquet'", "EXEC_ERROR"),
        ("l5-003", "DELETE FROM './data/orders/*.parquet'", "NON_SELECT"),
        ("l5-001", "SELECT 'UNANSWERABLE' AS reason", "CORRECT"),
        ("l5-001", "SELECT 0 AS shipping_cost", "WRONG_RESULT"),
        ("l1-001", "SELECT 'UNANSWERABLE' AS reason", "UNANSWERABLE"),
    ],
)
def test_classifier_covers_each_failure_mode(
    catalog: Catalog, data_dir: Path, tmp_path: Path, case_id: str, fake_sql: str, expected: str
) -> None:
    case = next(_rewrite(c, data_dir) for c in load_cases(CASES) if c.id == case_id)
    prov = FakeProvider({case.question: fake_sql.replace("./data", str(data_dir))})
    results = run_eval((case,), catalog, prov, run_id="t", out_dir=tmp_path)
    assert results[0].failure_mode == expected, results[0]
    assert (tmp_path / "t.parquet").exists() and (tmp_path / "traces" / "t.jsonl").exists()


def test_retrieval_miss_wins_over_correct_result(catalog: Catalog, tmp_path: Path) -> None:
    # 질문은 customers만 검색되지만 케이스는 orders를 요구 → 결과가 맞아도 RETRIEVAL_MISS
    case = EvalCase(
        id="x", question="How many customers?", golden_sql="SELECT 1", tables=("orders",)
    )
    prov = FakeProvider({}, default="SELECT 1")
    results = run_eval((case,), catalog, prov, top_k=1, run_id="t", out_dir=tmp_path)
    assert results[0].failure_mode == "RETRIEVAL_MISS"
    assert summarize(results)["RETRIEVAL_MISS"] == 1
