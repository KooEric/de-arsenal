import json
from datetime import UTC, datetime
from pathlib import Path

from _projects import minimal_project, production_project
from typer.testing import CliRunner

from censor.audit import audit_project
from censor.cli import app, run_cli
from censor.history import save_report

runner = CliRunner()


def test_audit_prints_json_to_stdout_and_exits_1_on_failures(tmp_path: Path) -> None:
    root = minimal_project(tmp_path)
    result = runner.invoke(app, ["audit", str(root), "--json-only"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["tier"] == "quick"
    assert (root / ".arsenal" / "censor" / "runs" / f"{payload['run_id']}.json").is_file()


def test_clean_project_exits_0_and_writes_summary_to_stderr(tmp_path: Path) -> None:
    root = production_project(tmp_path)
    result = runner.invoke(app, ["audit", str(root)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["grade"] in ("A+", "A")
    assert "grade" in result.stderr.lower()


def test_missing_project_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["audit", str(tmp_path / "nope")])
    assert result.exit_code == 2
    assert "arsenal.yaml" in result.stderr


def test_history_write_failure_exits_2_but_still_prints_json(tmp_path: Path) -> None:
    root = minimal_project(tmp_path)
    (root / ".arsenal").write_text("not a directory")
    result = runner.invoke(app, ["audit", str(root)])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["run_id"]
    assert "could not save" in result.stderr


def test_report_and_compare(tmp_path: Path) -> None:
    root = minimal_project(tmp_path)
    first = audit_project(root, now=datetime(2026, 11, 25, 9, 0, tzinfo=UTC))
    save_report(first, root, markdown="")
    production_project(root)
    second = audit_project(root, now=datetime(2026, 11, 25, 10, 0, tzinfo=UTC))
    save_report(second, root, markdown="")
    listing = runner.invoke(app, ["report", str(root)])
    assert listing.exit_code == 0
    assert first.run_id in listing.stdout and second.run_id in listing.stdout
    diff = runner.invoke(app, ["compare", first.run_id, second.run_id, "--project", str(root)])
    assert diff.exit_code == 0
    assert "guards.validate:collect.yaml: fail -> pass" in diff.stdout


def test_compare_unknown_run_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["compare", "a", "b", "--project", str(tmp_path)])
    assert result.exit_code == 2


def test_run_cli_returns_exit_code(tmp_path: Path) -> None:
    assert run_cli(["audit", str(production_project(tmp_path)), "--json-only"]) == 0
    assert run_cli(["audit", str(tmp_path / "missing")]) == 2
    assert run_cli(["no-such-command"]) == 2
