from augur.catalog import Catalog
from augur.generate import ask, extract_sql, is_select
from augur.llm import FakeProvider


def test_extract_sql_strips_fence_and_semicolon() -> None:
    assert extract_sql("```sql\nSELECT 1;\n```") == "SELECT 1"
    assert extract_sql("SELECT 2") == "SELECT 2"


def test_is_select_blocks_dml() -> None:
    assert is_select("WITH x AS (SELECT 1) SELECT * FROM x")
    assert not is_select("DELETE FROM t")


def test_ask_success_records_trace(catalog: Catalog) -> None:
    orders = catalog.get("orders")
    assert orders is not None
    prov = FakeProvider({"How many orders": f"SELECT count(*) FROM '{orders.path}'"})
    trace, rows = ask("How many orders are there?", catalog, prov)
    assert trace.executed and trace.error is None and rows == [(400,)]
    assert trace.retrieved_tables[0] == "orders" and trace.repair_attempts == 0


def test_ask_non_select_is_rejected_before_execution(catalog: Catalog) -> None:
    prov = FakeProvider({"Delete": "DELETE FROM './x.parquet'"})
    trace, rows = ask("Delete all orders", catalog, prov)
    assert (
        not trace.executed and trace.error and trace.error.startswith("NON_SELECT") and rows == []
    )


def test_ask_repairs_once_then_gives_up(catalog: Catalog) -> None:
    orders = catalog.get("orders")
    assert orders is not None
    bad = f"SELECT nope FROM '{orders.path}'"
    prov = FakeProvider({"Fixed SQL": bad, "How many": bad})  # 수정 요청에도 같은 오답
    trace, rows = ask("How many orders?", catalog, prov)
    assert not trace.executed and trace.repair_attempts == 1 and len(trace.repair_raw) == 1
    assert trace.error and "nope" in trace.error and rows == []
