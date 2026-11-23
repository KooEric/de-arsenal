"""`arsenal dataset apply` also publishes the snapshot to a Postgres table."""

import json
from pathlib import Path
from typing import Any

import duckdb
import pytest
from arsenal.cli import app
from typer.testing import CliRunner

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("testcontainers.postgres")

DSN_ENV = "ARSENAL_TEST_DATASET_PG"


@pytest.fixture
def pg(postgres_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    monkeypatch.setenv(DSN_ENV, postgres_url)
    monkeypatch.chdir(tmp_path)
    with psycopg.connect(postgres_url, autocommit=True) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS ledger")
    return postgres_url


def _run(*args: str, success: bool = True) -> dict[str, Any]:
    result = CliRunner().invoke(app, ["dataset", *args])
    assert result.exit_code == (0 if success else 1), (result.output, result.exception)
    return json.loads(result.output) if success else {"output": result.output}


def _rows(url: str, query: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(url) as con:
        return con.execute(query).fetchall()


def _target(table: str) -> list[str]:
    return ["--postgres-table", f"ledger.{table}", "--postgres-dsn-env", DSN_ENV]


def test_apply_publishes_the_snapshot_and_preview_does_not(pg: str, tmp_path: Path) -> None:
    (tmp_path / "pay.csv").write_text("emp_id,amount\n00123,1234\n00456,99.5\n")
    rules = ["--files", "*.csv", "--key", "emp_id", *_target("payroll")]
    first = _run("preview", *rules)
    assert first["publish"] == {"postgres": "ledger.payroll"}
    assert _rows(pg, "SELECT to_regclass('ledger.payroll')") == [(None,)]

    applied = _run("apply", *rules, "--expect", first["version"])
    assert applied["published_to"]["postgres"] == {"table": "ledger.payroll", "rows": 2}
    assert _rows(pg, "SELECT emp_id, amount FROM ledger.payroll ORDER BY emp_id") == [
        ("00123", "1234"),
        ("00456", "99.5"),
    ]


def test_a_new_apply_replaces_rows_and_keeps_views_and_grants(pg: str, tmp_path: Path) -> None:
    source = tmp_path / "pay.csv"
    source.write_text("emp_id,amount\n001,10\n002,20\n")
    rules = ["--files", "*.csv", "--key", "emp_id", *_target("replace_me")]
    _run("apply", *rules)
    with psycopg.connect(pg, autocommit=True) as con:
        con.execute(
            "CREATE VIEW ledger.replace_me_total AS SELECT count(*) AS n FROM ledger.replace_me"
        )
        con.execute("CREATE ROLE ledger_reader")
        con.execute("GRANT SELECT ON ledger.replace_me TO ledger_reader")

    source.write_text("emp_id,amount\n002,25\n003,30\n")
    _run("apply")  # later runs reuse the saved rules, the Postgres target included
    assert _rows(pg, "SELECT emp_id, amount FROM ledger.replace_me ORDER BY emp_id") == [
        ("002", "25"),
        ("003", "30"),
    ]
    assert _rows(pg, "SELECT n FROM ledger.replace_me_total") == [(2,)]
    assert _rows(
        pg,
        "SELECT has_table_privilege('ledger_reader', 'ledger.replace_me', 'SELECT')",
    ) == [(True,)]


def test_postgres_failure_fails_the_apply_and_keeps_both_publications(
    pg: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "pay.csv"
    source.write_text("emp_id,amount\n001,10\n")
    rules = ["--files", "*.csv", "--key", "emp_id", *_target("kept")]
    _run("apply", *rules)

    source.write_text("emp_id,amount\n001,99\n")
    monkeypatch.setenv(DSN_ENV, "postgresql://nobody:wrong@127.0.0.1:1/none")
    failed = _run("apply", success=False)
    assert "Postgres" in failed["output"]
    assert "wrong" not in failed["output"]  # the DSN password never reaches the output

    monkeypatch.setenv(DSN_ENV, pg)
    assert _rows(pg, "SELECT amount FROM ledger.kept") == [("10",)]
    with duckdb.connect(".arsenal/dataset.duckdb", read_only=True) as db:
        assert db.execute("SELECT amount FROM published").fetchall() == [("10",)]
    status = json.loads(CliRunner().invoke(app, ["status"]).output)["dataset"]
    assert status["state"] == "failed"


def test_existing_table_with_other_columns_is_refused(pg: str, tmp_path: Path) -> None:
    with psycopg.connect(pg, autocommit=True) as con:
        con.execute("CREATE TABLE ledger.other (id text, note text)")
        con.execute("INSERT INTO ledger.other VALUES ('x', 'keep me')")
    (tmp_path / "pay.csv").write_text("emp_id,amount\n001,10\n")
    failed = _run("apply", "--files", "*.csv", *_target("other"), success=False)
    assert "ledger.other" in failed["output"]
    assert _rows(pg, "SELECT note FROM ledger.other") == [("keep me",)]


def test_missing_schema_is_reported_not_created(pg: str, tmp_path: Path) -> None:
    (tmp_path / "pay.csv").write_text("emp_id\n001\n")
    rules = ["--files", "*.csv", "--postgres-table", "nope.payroll", "--postgres-dsn-env", DSN_ENV]
    failed = _run("apply", *rules, success=False)
    assert "nope" in failed["output"]
    assert _rows(pg, "SELECT to_regnamespace('nope')") == [(None,)]
