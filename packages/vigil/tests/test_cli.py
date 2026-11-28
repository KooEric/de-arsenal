import json
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from _events import write_events
from typer.testing import CliRunner, Result

from vigil.cli import app
from vigil.events import EVENT_COLUMNS

runner = CliRunner()

RULE = """\
id: {id}
title: Root console login
severity: {severity}
attack: [T1078.004]
source: cloudtrail
query: SELECT * FROM events WHERE action = 'ConsoleLogin' AND actor_type = 'Root'
tests:
  match:
    - {{ action: ConsoleLogin, actor_type: {match_actor} }}
  no_match:
    - {{ action: ConsoleLogin, actor_type: IAMUser }}
"""


def write_rule(
    rules_dir: Path, rule_id: str = "root-login", severity: str = "high", match_actor: str = "Root"
) -> None:
    rules_dir.mkdir(exist_ok=True)
    text = RULE.format(id=rule_id, severity=severity, match_actor=match_actor)
    (rules_dir / f"{rule_id}.yaml").write_text(text, encoding="utf-8")


def test_test_command_passes(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules")
    result = runner.invoke(app, ["test", "--rules", str(tmp_path / "rules")])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"rules": 1, "failed": {}}


def test_test_command_fails_with_exit_1(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules", match_actor="IAMUser")
    result = runner.invoke(app, ["test", "--rules", str(tmp_path / "rules")])
    assert result.exit_code == 1, result.output
    assert list(json.loads(result.stdout)["failed"]) == ["root-login"]


def test_missing_rules_directory_is_exit_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["test", "--rules", str(tmp_path / "nope")])
    assert result.exit_code == 2, result.output


def test_fixture_typo_is_exit_2_and_names_the_rule(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules")
    path = tmp_path / "rules" / "root-login.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "{ action: ConsoleLogin, actor_type: Root }", "{ acton: ConsoleLogin }"
        ),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["test", "--rules", str(tmp_path / "rules")])
    assert result.exit_code == 2, result.output
    assert "root-login" in result.output
    assert "Traceback" not in result.output


def test_invalid_rule_file_is_exit_2(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules")
    (tmp_path / "rules" / "bad.yaml").write_text("id: nope\n", encoding="utf-8")
    result = runner.invoke(app, ["test", "--rules", str(tmp_path / "rules")])
    assert result.exit_code == 2, result.output


def _lake(tmp_path: Path) -> str:
    write_events(
        tmp_path / "events" / "a.parquet",
        [{"event_id": "e1", "actor_type": "Root", "action": "ConsoleLogin",
          "event_time": datetime(2026, 11, 1, 10)}],
    )  # fmt: skip
    return str(tmp_path / "events")


def _run(tmp_path: Path, *extra: str, out: str = "alerts") -> Result:
    return runner.invoke(
        app,
        ["run", "--rules", str(tmp_path / "rules"), "--events", _lake(tmp_path),
         "--out", str(tmp_path / out), *extra],
    )  # fmt: skip


def test_run_command_reports_new_alerts_then_none(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules")
    first = _run(tmp_path)
    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout)["new_alerts"] == 1
    second = _run(tmp_path)
    assert json.loads(second.stdout)["new_alerts"] == 0


def test_fail_on_exits_1_only_at_or_above_the_threshold(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules", severity="medium")
    assert _run(tmp_path, "--fail-on", "high").exit_code == 0
    assert _run(tmp_path, "--fail-on", "medium", out="alerts-2").exit_code == 1


def test_run_without_event_files_is_exit_2(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules")
    result = runner.invoke(
        app,
        ["run", "--rules", str(tmp_path / "rules"), "--events", str(tmp_path / "none"),
         "--out", str(tmp_path / "alerts")],
    )  # fmt: skip
    assert result.exit_code == 2, result.output


def test_coverage_command_prints_the_report(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules")
    result = runner.invoke(
        app, ["coverage", "--rules", str(tmp_path / "rules"), "--events", _lake(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["rules"] == 1
    assert "T1530" in payload["uncovered"]


def test_coverage_json_only_keeps_stderr_empty(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules")
    result = runner.invoke(app, ["coverage", "--rules", str(tmp_path / "rules"), "--json-only"])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""


def test_unpack_command(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.json").write_text(json.dumps({"Records": [{"eventID": "e1"}]}), encoding="utf-8")
    out = tmp_path / "events.jsonl"
    result = runner.invoke(app, ["unpack", str(src), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"events": 1, "out": str(out)}
    assert runner.invoke(app, ["unpack", str(tmp_path / "nope"), "--out", str(out)]).exit_code == 2


def test_out_that_is_a_file_is_exit_2(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules")
    (tmp_path / "alerts").write_text("x", encoding="utf-8")
    result = _run(tmp_path)
    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert str(tmp_path / "alerts") in result.output


def test_corrupt_parquet_in_out_is_exit_2(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules")
    (tmp_path / "alerts").mkdir()
    (tmp_path / "alerts" / "junk.parquet").write_bytes(b"junk!")
    result = _run(tmp_path)
    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "junk.parquet" in result.output


def test_text_event_time_in_the_lake_is_exit_2(tmp_path: Path) -> None:
    write_rule(tmp_path / "rules")
    columns: dict[str, list[object]] = {name: ["x"] for name in EVENT_COLUMNS}
    columns["mfa_used"] = [True]
    (tmp_path / "events").mkdir()
    pq.write_table(pa.table(columns), tmp_path / "events" / "a.parquet")  # pyright: ignore[reportUnknownMemberType]
    result = runner.invoke(
        app,
        ["run", "--rules", str(tmp_path / "rules"), "--events", str(tmp_path / "events"),
         "--out", str(tmp_path / "alerts")],
    )  # fmt: skip
    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "event_time" in result.output
