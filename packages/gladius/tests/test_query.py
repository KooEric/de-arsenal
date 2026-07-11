"""즉석 SQL 쿼리 — 미니 DWH (M3 Task 3.6)."""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from typer.testing import CliRunner

from gladius.cli import app
from gladius.engine import query

runner = CliRunner()


def test_query_reads_parquet_paths_directly(tmp_path: Path) -> None:
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        pa.table({"id": [1, 2]}), tmp_path / "a.parquet"
    )
    result = query(f"SELECT count(*) AS n FROM '{tmp_path}/*.parquet'")
    assert result.to_pylist() == [{"n": 2}]


def test_query_cli_formats(tmp_path: Path) -> None:
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        pa.table({"id": [1, 2]}), tmp_path / "a.parquet"
    )
    sql = f"SELECT count(*) AS n FROM '{tmp_path}/*.parquet'"

    table_result = runner.invoke(app, ["query", sql])
    assert table_result.exit_code == 0
    assert "n" in table_result.output
    assert "2" in table_result.output

    csv_result = runner.invoke(app, ["query", sql, "--format", "csv"])
    assert csv_result.exit_code == 0
    lines = csv_result.output.strip().splitlines()
    assert lines[0].strip('"') == "n"
    assert lines[1] == "2"

    jsonl_result = runner.invoke(app, ["query", sql, "--format", "jsonl"])
    assert jsonl_result.exit_code == 0
    assert json.loads(jsonl_result.output.strip()) == {"n": 2}


def test_invalid_sql_shows_duckdb_error_and_exit_1() -> None:
    result = runner.invoke(app, ["query", "SELECT * FROM nonexistent_table_xyz"])
    assert result.exit_code == 1
    assert "error:" in result.output
