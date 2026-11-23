import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import pytest
from arsenal.cli import app
from typer.testing import CliRunner


def test_sensor_revisions_and_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "sensor.csv"
    source.write_text("id,day,reading\n001,2026-01-01,2\n001,2026-01-01,2\n")
    rules = [
        "--files",
        "*.csv",
        "--key",
        "id",
        "--time-column",
        "day",
        "--measure",
        "reading",
        "--distinct",
    ]
    runner = CliRunner()

    def command(*args: str, success: bool = True) -> str:
        result = runner.invoke(app, list(args))
        assert result.exit_code == (0 if success else 1), (result.output, result.exception)
        return result.output

    def report(*args: str) -> dict[str, Any]:
        return json.loads(command("dataset", *args))

    first = report("preview", *rules)
    assert not (tmp_path / ".arsenal").exists()
    assert first["source"]["rows"] == 2 and first["result"]["total"] == 2
    assert first["excluded"]["examples"][0]["id"] == "001"
    report("apply", *rules, "--expect", first["version"])
    same = report("preview")
    assert same["files"] == {"sensor.csv": "unchanged"}
    source.write_text("id,day,reading\n001,2026-01-01,3\n")
    (tmp_path / "new.csv").write_text("reading,day,id\n9,2026-01-02,002\n")
    update = report("preview")
    assert update["files"] == {"new.csv": "new", "sensor.csv": "revised"}
    assert update["changes"]["updated"]["examples"][0]["before"]["reading"] == "2"
    assert update["changes"]["added"]["count"] == 1
    command("dataset", "apply", "--expect", same["version"], success=False)
    failed = json.loads(command("status", success=False))["dataset"]
    assert failed["state"] == "failed" and failed["result"]["rows"] == 1
    assert failed["last_success"] and failed["recovery"]
    report("apply", "--expect", update["version"])
    assert report("status")["result"]["last"].startswith("2026-01-02")
    with duckdb.connect(".arsenal/dataset.duckdb", read_only=True) as db:
        assert db.execute("SELECT id FROM published ORDER BY id").fetchall() == [("001",), ("002",)]
    source.unlink()
    assert report("preview")["changes"]["removed"]["count"] == 1
    (tmp_path / "new.csv").unlink()
    command("dataset", "apply", success=False)
    assert "입력 파일" in json.loads(command("status", success=False))["dataset"]["error"]


def test_events_without_identifiers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    events = tmp_path / "events.jsonl"
    events.write_text('{"event":"open"}\n{"event":"open"}\n')
    runner = CliRunner()
    first = runner.invoke(app, ["dataset", "apply", "--files", "*.jsonl", "--max-age-hours", "0"])
    assert first.exit_code == 0, first.output
    status = runner.invoke(app, ["status"])
    assert status.exit_code == 1 and json.loads(status.output)["dataset"]["state"] == "stale"
    events.write_text('{"event":"open"}\n')
    change = json.loads(runner.invoke(app, ["dataset", "preview"]).output)
    assert change["changes"]["removed"] == {"count": 1, "examples": [{"event": "open"}]}
    assert change["excluded"]["count"] == 0


def test_parquet_schema_change_preserves_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with duckdb.connect() as db:
        db.execute("COPY (SELECT 7 AS sample) TO 'a.parquet'")
    runner = CliRunner()
    published = runner.invoke(app, ["dataset", "apply", "--files", "*.parquet"])
    assert published.exit_code == 0, published.output
    assert json.loads(published.output)["changes"]["added"]["examples"] == [{"sample": 7}]
    with duckdb.connect() as db:
        db.execute("COPY (SELECT 'outage' AS event) TO 'b.parquet'")
    assert runner.invoke(app, ["dataset", "apply"]).exit_code == 1
    result = runner.invoke(
        app,
        [
            "query",
            "ATTACH '.arsenal/dataset.duckdb' AS ds (READ_ONLY); SELECT * FROM ds.published",
            "--format",
            "jsonl",
        ],
    )
    assert result.exit_code == 0 and json.loads(result.output) == {"sample": 7}


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _published_types() -> dict[str, str]:
    with duckdb.connect(".arsenal/dataset.duckdb", read_only=True) as db:
        return {row[0]: row[1] for row in db.execute("DESCRIBE published").fetchall()}


def test_jsonl_column_that_is_null_in_every_row_is_not_a_schema_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    snapshot = tmp_path / "cards.jsonl"
    _jsonl(snapshot, [{"id": "a", "fee": 100, "url": "x"}, {"id": "b", "fee": 200, "url": None}])
    first = runner.invoke(app, ["dataset", "apply", "--files", "*.jsonl", "--key", "id"])
    assert first.exit_code == 0, first.output
    assert _published_types() == {"id": "VARCHAR", "fee": "BIGINT", "url": "VARCHAR"}

    _jsonl(snapshot, [{"id": "a", "fee": None, "url": None}, {"id": "b", "fee": None, "url": None}])
    preview = runner.invoke(app, ["dataset", "preview"])
    assert preview.exit_code == 0, preview.output
    changes = json.loads(preview.output)["changes"]
    assert (changes["added"]["count"], changes["removed"]["count"]) == (0, 0)
    assert changes["updated"]["count"] == 2
    assert changes["updated"]["examples"][0]["before"]["fee"] in (100, 200)
    assert changes["updated"]["examples"][0]["after"]["fee"] is None
    assert runner.invoke(app, ["dataset", "apply"]).exit_code == 0
    # The emptied columns keep the types they were published with.
    assert _published_types() == {"id": "VARCHAR", "fee": "BIGINT", "url": "VARCHAR"}

    _jsonl(snapshot, [{"id": "a", "fee": 300, "url": "y"}, {"id": "b", "fee": None, "url": None}])
    back = runner.invoke(app, ["dataset", "apply"])
    assert back.exit_code == 0, back.output
    assert json.loads(back.output)["changes"]["updated"]["count"] == 1
    assert runner.invoke(app, ["status"]).exit_code == 0


def test_jsonl_column_first_published_empty_takes_its_type_from_later_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    snapshot = tmp_path / "cards.jsonl"
    _jsonl(snapshot, [{"id": "a", "fee": None}])
    assert (
        runner.invoke(app, ["dataset", "apply", "--files", "*.jsonl", "--key", "id"]).exit_code == 0
    )
    _jsonl(snapshot, [{"id": "a", "fee": 100}, {"id": "b", "fee": 200}])
    result = runner.invoke(app, ["dataset", "apply"])
    assert result.exit_code == 0, result.output
    changes = json.loads(result.output)["changes"]
    assert (changes["added"]["count"], changes["updated"]["count"]) == (1, 1)
    assert _published_types()["fee"] == "BIGINT"


def test_jsonl_real_type_change_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    snapshot = tmp_path / "cards.jsonl"
    _jsonl(snapshot, [{"id": "a", "fee": 100}])
    assert (
        runner.invoke(app, ["dataset", "apply", "--files", "*.jsonl", "--key", "id"]).exit_code == 0
    )
    _jsonl(snapshot, [{"id": "a", "fee": "free"}])
    refused = runner.invoke(app, ["dataset", "apply"])
    assert refused.exit_code == 1
    assert "자료형" in refused.output
    assert _published_types()["fee"] == "BIGINT"
    with duckdb.connect(".arsenal/dataset.duckdb", read_only=True) as db:
        assert db.execute("SELECT fee FROM published").fetchall() == [(100,)]


def test_jsonl_files_of_one_snapshot_may_differ_in_an_empty_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    _jsonl(tmp_path / "part-1.jsonl", [{"id": "a", "fee": None}])
    _jsonl(tmp_path / "part-2.jsonl", [{"id": "b", "fee": 200}])
    _jsonl(tmp_path / "part-3.jsonl", [{"id": "c", "fee": None}])
    result = runner.invoke(app, ["dataset", "apply", "--files", "*.jsonl", "--key", "id"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["result"]["rows"] == 3
    assert _published_types()["fee"] == "BIGINT"
    _jsonl(tmp_path / "part-3.jsonl", [{"id": "c", "fee": "free"}])
    assert runner.invoke(app, ["dataset", "apply"]).exit_code == 1


def _workbook(path: Path, rows: list[list[Any]]) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    book.save(path)


def _dataset(*args: str, success: bool = True) -> dict[str, Any]:
    result = CliRunner().invoke(app, ["dataset", *args])
    assert result.exit_code == (0 if success else 1), (result.output, result.exception)
    return json.loads(result.output) if success else {"output": result.output}


def test_excel_ledger_is_read_as_text_and_diffed_by_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A payroll ledger keeps leading zeros and whole numbers the way they look in Excel,
    like a CSV read as text, so a monthly re-export only reports the rows that changed."""
    pytest.importorskip("fastexcel")
    monkeypatch.chdir(tmp_path)
    _workbook(
        tmp_path / "payroll.xlsx",
        [["emp_id", "name", "amount"], ["00123", "Kim", 1234], ["00456", "Lee", 99.5]],
    )
    rules = ["--files", "*.xlsx", "--key", "emp_id", "--measure", "amount"]
    first = _dataset("preview", *rules)
    assert first["source"]["rows"] == 2
    _dataset("apply", *rules, "--expect", first["version"])
    with duckdb.connect(".arsenal/dataset.duckdb", read_only=True) as db:
        assert db.execute("SELECT emp_id, amount FROM published ORDER BY emp_id").fetchall() == [
            ("00123", "1234"),
            ("00456", "99.5"),
        ]

    _workbook(
        tmp_path / "payroll.xlsx",
        [["emp_id", "name", "amount"], ["00123", "Kim", 1300], ["00456", "Lee", 99.5]],
    )
    update = _dataset("preview")
    assert update["files"] == {"payroll.xlsx": "revised"}
    assert update["changes"]["updated"]["count"] == 1
    assert update["changes"]["updated"]["examples"][0]["before"]["amount"] == "1234"
    assert update["changes"]["added"]["count"] == 0


def test_excel_lock_file_of_an_open_workbook_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Excel leaves `~$name.xlsx` next to a workbook someone has open. It is not data."""
    pytest.importorskip("fastexcel")
    monkeypatch.chdir(tmp_path)
    _workbook(tmp_path / "ledger.xlsx", [["id", "v"], ["1", "a"]])
    (tmp_path / "~$ledger.xlsx").write_bytes(b"\x00owner")
    report = _dataset("preview", "--files", "*.xlsx", "--key", "id")
    assert report["files"] == {"ledger.xlsx": "new"}
    assert report["source"]["rows"] == 1


def test_unreadable_workbook_names_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("fastexcel")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "broken.xlsb").write_bytes(b"not a workbook")
    result = _dataset("preview", "--files", "*.xlsb", success=False)
    assert "broken.xlsb" in result["output"]


def test_excel_and_csv_cannot_be_mixed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.csv").write_text("id\n1\n")
    (tmp_path / "b.xlsx").write_bytes(b"x")
    result = _dataset("preview", "--files", "*.*", success=False)
    assert "Excel" in result["output"]


def test_excel_without_extra_says_how_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "fastexcel", None)
    (tmp_path / "ledger.xlsx").write_bytes(b"x")
    result = _dataset("preview", "--files", "*.xlsx", success=False)
    assert "de-arsenal[excel]" in result["output"]


@pytest.mark.parametrize(
    "args",
    [
        ["--postgres-table", "ledger.payroll"],
        ["--postgres-dsn-env", "PG"],
        ["--postgres-table", "payroll", "--postgres-dsn-env", "PG"],
    ],
)
def test_postgres_target_needs_schema_table_and_dsn_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.csv").write_text("id\n1\n")
    result = _dataset("preview", "--files", "*.csv", *args, success=False)
    assert "--postgres" in result["output"]


def test_postgres_dsn_env_must_be_set_before_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ARSENAL_UNSET_PG", raising=False)
    (tmp_path / "a.csv").write_text("id\n1\n")
    args = ["--postgres-table", "ledger.t", "--postgres-dsn-env", "ARSENAL_UNSET_PG"]
    result = _dataset("apply", "--files", "*.csv", *args, success=False)
    assert "ARSENAL_UNSET_PG" in result["output"]
    status = json.loads(CliRunner().invoke(app, ["status"]).output)["dataset"]
    assert status["state"] == "failed"
