import json
from pathlib import Path

import pytest
from _data import DUPLICATE, ROWS, write_duckdb, write_parquet
from _projects import production_project
from typer.testing import CliRunner

from censor import limits
from censor.cli import app
from censor.model import AuditReport

SCHEMA = Path(__file__).parents[3] / "schemas" / "censor-report.json"
runner = CliRunner()


def _audit(*args: str) -> tuple[int, dict[str, object], str]:
    result = runner.invoke(app, ["audit", *args])
    payload: dict[str, object] = json.loads(result.stdout) if result.stdout.strip() else {}
    return result.exit_code, payload, result.stderr


def _statuses(payload: dict[str, object]) -> dict[str, str]:
    checks: list[dict[str, str]] = payload["checks"]  # type: ignore[assignment]
    return {c["id"].split(":")[0]: c["status"] for c in checks}


def test_files_target_is_standard_and_saves_history_in_the_base_dir(tmp_path: Path) -> None:
    write_parquet(tmp_path / "data" / "a.parquet", ROWS)
    pattern = str(tmp_path / "data" / "*.parquet")
    code, payload, stderr = _audit(str(tmp_path), "--files", pattern, "--key", "id")
    assert code == 0, stderr
    assert (payload["tier"], payload["target_kind"], payload["target"]) == (
        "standard",
        "files",
        pattern,
    )
    assert _statuses(payload)["data_health.key_unique"] == "pass"
    assert set(payload) == set(json.loads(SCHEMA.read_text())["properties"])
    assert AuditReport.model_validate(payload).target_kind == "files"
    run = tmp_path / ".arsenal" / "censor" / "runs" / f"{payload['run_id']}.json"
    assert run.is_file() and str(payload["run_id"]).endswith("-standard")


def test_duplicate_key_exits_1(tmp_path: Path) -> None:
    write_parquet(tmp_path / "a.parquet", [*ROWS, DUPLICATE])
    code, payload, _ = _audit(
        str(tmp_path), "--files", str(tmp_path / "*.parquet"), "--key", "id,v", "--key", "ts"
    )
    checks: list[dict[str, str]] = payload["checks"]  # type: ignore[assignment]
    unique = next(c for c in checks if c["id"].startswith("data_health.key_unique"))
    assert unique["evidence"] == "(id, v, ts) is unique"
    code, payload, _ = _audit(str(tmp_path), "--files", str(tmp_path / "*.parquet"), "--key", "id")
    assert code == 1 and _statuses(payload)["data_health.key_unique"] == "fail"


def test_duckdb_target_with_sample_since_and_time_column(tmp_path: Path) -> None:
    store = write_duckdb(tmp_path / "x.duckdb", "published", 20_000)
    code, payload, stderr = _audit(
        str(tmp_path),
        "--duckdb",
        str(store),
        "--table",
        "published",
        "--key",
        "id",
        "--time-column",
        "ts",
        "--sample",
        "10%",
        "--since",
        "2026-11-01",
        "--max-age-hours",
        "1",
    )
    assert code == 1, stderr  # the data is older than an hour
    statuses = _statuses(payload)
    assert statuses["data_health.key_unique"] == "warn"
    assert statuses["freshness.data_age"] == "fail"
    assert payload["sample"] == "10% of the keys (by key hash)" and payload["since"] == "2026-11-01"
    markdown = next((tmp_path / ".arsenal" / "censor" / "reports").glob("*.md")).read_text()
    assert "sample: 10% of the keys (by key hash)" in markdown and "since: 2026-11-01" in markdown


def test_scale_guard_prints_guidance_on_stderr_and_in_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(3):
        write_parquet(tmp_path / "data" / f"{i}.parquet", ROWS)
    monkeypatch.setattr(limits, "MAX_FILES", 2)
    pattern = str(tmp_path / "data" / "*.parquet")
    code, payload, stderr = _audit(str(tmp_path), "--files", pattern)
    assert code == 0
    assert "Pick one:" in stderr and "--sample 1%" in stderr
    assert payload["grade"] is None
    markdown = next((tmp_path / ".arsenal" / "censor" / "reports").glob("*.md")).read_text()
    assert "## Scale and sample" in markdown and "Pick one:" in markdown
    quiet = runner.invoke(app, ["audit", str(tmp_path), "--files", pattern, "--json-only"])
    assert quiet.stderr == ""


def test_unreadable_target_prints_the_report_then_exits_2(tmp_path: Path) -> None:
    store = write_duckdb(tmp_path / "x.duckdb", "t", 5)
    code, payload, stderr = _audit(str(tmp_path), "--duckdb", str(store), "--table", "nope")
    assert code == 2
    assert _statuses(payload) == {"freshness.data_age": "na", "data_health.skipped": "na"}
    assert "censor: no table nope" in stderr
    assert list((tmp_path / ".arsenal" / "censor" / "runs").glob("*.json"))


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--files", "nope/*.parquet"], "no file matches"),
        (["--files", "a", "--duckdb", "b", "--table", "t"], "one data target"),
        (["--table", "t"], "--table needs --duckdb or --postgres-dsn-env"),
        (["--duckdb", "x.duckdb"], "--duckdb needs --table"),
        (["--postgres-dsn-env", "X"], "--postgres-dsn-env needs --table"),
        (["--duckdb", "x", "--table", "t", "--postgres-dsn-env", "X"], "one data target"),
        (["--files", "a", "--tier", "quick"], "needs --tier standard"),
        (["--files", "a", "--sample", "lots"], "--sample"),
        (["--files", "a", "--sample", "0"], "--sample"),
        (["--files", "a", "--since", "yesterday"], "--since"),
        (["--files", "a", "--since", "2026-11-01"], "--since needs --time-column"),
        (["--table", "t", "--postgres-dsn-env", "CENSOR_UNSET_VAR"], "CENSOR_UNSET_VAR"),
        (["--files", "a", "--memory-limit", "big"], "--memory-limit"),
        (["--files", "a", "--timeout", "0"], "--timeout"),
    ],
)
def test_bad_arguments_exit_2_without_a_report(
    tmp_path: Path, args: list[str], message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CENSOR_UNSET_VAR", raising=False)
    result = runner.invoke(app, ["audit", str(tmp_path), *args])
    assert result.exit_code == 2, result.output
    assert result.stdout == "" and message in result.stderr
    assert not (tmp_path / ".arsenal").exists()


def test_project_standard_tier_scores_and_keeps_quick_checks(tmp_path: Path) -> None:
    root = production_project(tmp_path)
    quick_code, quick, _ = _audit(str(root), "--tier", "quick", "--json-only")
    code, standard, stderr = _audit(str(root), "--tier", "standard")
    assert (quick_code, code) == (0, 0), stderr
    assert standard["tier"] == "standard" and standard["score"] == quick["score"] == 100.0
    quick_checks: list[object] = quick["checks"]  # type: ignore[assignment]
    standard_checks: list[object] = standard["checks"]  # type: ignore[assignment]
    assert standard_checks[: len(quick_checks)] == quick_checks
    assert _statuses(standard)["reliability.failed_units"] == "na"  # never run


def test_report_on_a_missing_directory_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["report", str(tmp_path / "nope")])
    assert result.exit_code == 2 and "no such directory" in result.stderr
    assert runner.invoke(app, ["report", str(tmp_path)]).exit_code == 0


def test_unreadable_manifest_exits_2(tmp_path: Path) -> None:
    (tmp_path / "arsenal.yaml").write_bytes(b"\xff\xfe\x00")
    result = runner.invoke(app, ["audit", str(tmp_path)])
    assert result.exit_code == 2 and "cannot read" in result.stderr
