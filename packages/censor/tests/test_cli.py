import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pytest
from _projects import minimal_project, production_project
from typer.testing import CliRunner

from censor.audit import audit_project
from censor.cli import app, run_cli
from censor.history import save_report
from censor.model import AuditReport

SCHEMA = Path(__file__).parents[3] / "schemas" / "censor-report.json"

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


def _saved_run(root: Path) -> str:
    first = audit_project(root, now=datetime(2026, 11, 25, 9, 0, tzinfo=UTC))
    save_report(first, root, markdown="")
    return first.run_id


def test_report_skips_corrupt_run_with_warning(tmp_path: Path) -> None:
    root = minimal_project(tmp_path)
    run_id = _saved_run(root)
    (root / ".arsenal" / "censor" / "runs" / "bad.json").write_text("{oops")
    result = runner.invoke(app, ["report", str(root)])
    assert result.exit_code == 0, result.output
    assert run_id in result.stdout
    assert "bad.json" not in result.stdout
    assert "bad.json" in result.stderr


def test_compare_with_corrupt_run_exits_2_without_traceback(tmp_path: Path) -> None:
    root = minimal_project(tmp_path)
    run_id = _saved_run(root)
    (root / ".arsenal" / "censor" / "runs" / "bad.json").write_text("{oops")
    result = runner.invoke(app, ["compare", run_id, "bad", "--project", str(root)])
    assert result.exit_code == 2
    assert "unreadable" in result.stderr
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_compare_rejects_path_traversal_run_id(tmp_path: Path) -> None:
    result = runner.invoke(app, ["compare", "../x", "y", "--project", str(tmp_path)])
    assert result.exit_code == 2
    assert "invalid run id" in result.stderr


def test_parquet_failure_exits_2_with_json_and_no_partial_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise pa.ArrowInvalid("boom")

    monkeypatch.setattr("censor.history.pq.write_table", boom)
    root = minimal_project(tmp_path)
    result = runner.invoke(app, ["audit", str(root), "--json-only"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["run_id"]
    assert "could not save" in result.stderr
    runs = root / ".arsenal" / "censor" / "runs"
    assert list(runs.iterdir()) == []


def test_report_and_compare_without_history_dir(tmp_path: Path) -> None:
    listing = runner.invoke(app, ["report", str(tmp_path)])
    assert listing.exit_code == 0
    assert listing.stdout == ""
    diff = runner.invoke(app, ["compare", "a", "b", "--project", str(tmp_path)])
    assert diff.exit_code == 2


def test_symlinked_history_dir_exits_2_and_writes_nothing_outside(tmp_path: Path) -> None:
    root = minimal_project(tmp_path / "p")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".arsenal").symlink_to(outside, target_is_directory=True)
    result = runner.invoke(app, ["audit", str(root), "--json-only"])
    assert result.exit_code == 2
    assert "could not save" in result.stderr
    assert list(outside.iterdir()) == []


def test_tier_quick_is_the_default_and_accepted(tmp_path: Path) -> None:
    root = production_project(tmp_path)
    result = runner.invoke(app, ["audit", str(root), "--tier", "quick", "--json-only"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["tier"] == "quick"


def test_quick_takes_max_age_hours_for_the_file_time_check(tmp_path: Path) -> None:
    root = production_project(tmp_path)
    (root / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (root / "data" / "raw" / "a.parquet").write_bytes(b"x")
    result = runner.invoke(app, ["audit", str(root), "--max-age-hours", "2", "--json-only"])
    assert result.exit_code in (0, 1), result.output
    checks = {c["id"]: c for c in json.loads(result.stdout)["checks"]}
    fresh = [c for i, c in checks.items() if i.startswith("operability.freshness:")]
    assert fresh and all("limit 2h" in c["evidence"] for c in fresh)


def test_tier_standard_runs_and_is_recorded(tmp_path: Path) -> None:
    root = production_project(tmp_path)
    result = runner.invoke(app, ["audit", str(root), "--tier", "standard"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["tier"] == "standard"


def test_unknown_tier_is_a_usage_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["audit", str(production_project(tmp_path)), "--tier", "deep"])
    assert result.exit_code == 2


def test_same_second_runs_get_distinct_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fixed(tier: str, now: datetime | None = None) -> str:
        return f"20261125-093000-{tier}"

    monkeypatch.setattr("censor.audit.new_run_id", fixed)
    root = production_project(tmp_path)
    ids: list[str] = []
    for _ in range(3):
        result = runner.invoke(app, ["audit", str(root), "--json-only"])
        assert result.exit_code == 0, result.output
        ids.append(json.loads(result.stdout)["run_id"])
    assert ids == ["20261125-093000-quick", "20261125-093000-quick-2", "20261125-093000-quick-3"]
    runs = root / ".arsenal" / "censor" / "runs"
    for run_id in ids:
        saved = json.loads((runs / f"{run_id}.json").read_text())
        assert saved["run_id"] == run_id
        assert (runs / f"{run_id}.parquet").is_file()


def test_audit_stdout_matches_the_published_schema(tmp_path: Path) -> None:
    # jsonschema is not in the workspace: validate with the model and pin the committed schema
    # to the model, so stdout, AuditReport and schemas/censor-report.json cannot drift apart.
    result = runner.invoke(app, ["audit", str(minimal_project(tmp_path)), "--json-only"])
    report = AuditReport.model_validate_json(result.stdout)
    assert report.model_dump(mode="json") == json.loads(result.stdout)
    assert json.loads(SCHEMA.read_text(encoding="utf-8")) == AuditReport.model_json_schema()
