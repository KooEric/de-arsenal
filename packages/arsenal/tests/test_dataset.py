import json
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
