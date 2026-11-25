import os
from pathlib import Path

import duckdb
import pytest

from augur.catalog import Catalog
from augur.generate import allowed_dirs, ask, execute, extract_sql, is_select
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


GUARD_BYPASSES = [
    "-- totals\nCOPY (SELECT 42) TO 'a.csv'",
    "/* totals */ COPY (SELECT 42) TO 'b.csv'",
    "SELECT 1; COPY (SELECT 42) TO 'c.csv'",
    "EXPLAIN ANALYZE COPY (SELECT 42) TO 'd.csv'",
    "-- x\nATTACH 'e.db' AS e; CREATE TABLE e.t AS SELECT 1 AS a",
    "SELECT 1; DROP TABLE t",
]


@pytest.mark.parametrize("sql", GUARD_BYPASSES)
def test_is_select_blocks_comment_multi_statement_and_explain(sql: str) -> None:
    assert not is_select(sql)


def test_is_select_keeps_parse_errors_for_exec_error() -> None:
    assert is_select("SELEC count(*) FROM t")  # 실행 단계의 EXEC_ERROR로 남긴다
    assert is_select("(SELECT 1) UNION ALL (SELECT 2)")
    assert not is_select("   ")


@pytest.mark.parametrize("sql", GUARD_BYPASSES)
def test_ask_rejects_bypass_and_writes_nothing(
    sql: str, catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    trace, rows = ask("How many orders?", catalog, FakeProvider({"How many": sql}))
    assert trace.error and trace.error.startswith("NON_SELECT") and rows == []
    assert list(tmp_path.iterdir()) == []


def test_execute_reads_catalog_dirs_only(catalog: Catalog, tmp_path: Path) -> None:
    orders = catalog.get("orders")
    assert orders is not None
    dirs = allowed_dirs(catalog)
    _, rows = execute(f"SELECT count(*) FROM '{orders.path}'", dirs=dirs)
    assert rows == [(400,)]
    outside = tmp_path / "secret.csv"
    outside.write_text("a\n1\n", encoding="utf-8")
    with pytest.raises(duckdb.Error, match="Permission"):
        execute(f"SELECT * FROM read_csv('{outside.as_posix()}')", dirs=dirs)
    with pytest.raises(duckdb.Error, match="Permission"):
        execute("SELECT * FROM 'https://example.com/x.csv'", dirs=dirs)
    with pytest.raises(duckdb.Error):
        execute("SET enable_external_access = true", dirs=dirs)  # 잠긴 설정


def test_allowed_dirs_strips_glob(tmp_path: Path) -> None:
    from augur.catalog import Catalog as Cat
    from augur.catalog import SchemaDoc

    docs = (
        SchemaDoc(table="a", path=f"{tmp_path}/a/*.parquet", row_count=0, columns=()),
        SchemaDoc(table="b", path=f"{tmp_path}/b/part-0.parquet", row_count=0, columns=()),
    )
    got = allowed_dirs(Cat(tables=docs))
    assert got == tuple(sorted(os.path.join(str(tmp_path / n), "") for n in ("a", "b")))
