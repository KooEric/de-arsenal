import json
from pathlib import Path

from typer.testing import CliRunner

from vigil.cli import app

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
