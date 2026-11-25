import json
from pathlib import Path

from _projects import bare_project, write_project

from censor.checks.agent import check_agent_readiness
from censor.context import load_context
from censor.model import Status

SETTINGS = {
    "enabledPlugins": {"de-arsenal@de-arsenal": True},
    "permissions": {"deny": ["Bash(rm -rf .arsenal*)"]},
}
HOOK = "#!/bin/bash\n# scan staged *.yaml and *.yml files for secrets\n"
CLAUDE_MD = "Validate with `arsenal check`, then `arsenal run`.\n"


def _statuses(root: Path) -> dict[str, Status]:
    return {r.id: r.status for r in check_agent_readiness(load_context(root))}


def test_project_without_harness(tmp_path: Path) -> None:
    assert _statuses(bare_project(tmp_path)) == {
        "agent.claude_md": Status.FAIL,
        "agent.plugin": Status.WARN,
        "agent.deny_state": Status.WARN,
        "agent.secret_hook": Status.WARN,
    }


def test_full_harness_passes(tmp_path: Path) -> None:
    bare_project(tmp_path)
    write_project(
        tmp_path,
        {
            "CLAUDE.md": CLAUDE_MD,
            ".claude/settings.json": json.dumps(SETTINGS),
            ".claude/hooks/secret-scan.sh": HOOK,
        },
    )
    assert set(_statuses(tmp_path).values()) == {Status.PASS}


def test_claude_md_without_arsenal_commands_warns(tmp_path: Path) -> None:
    bare_project(tmp_path)
    write_project(tmp_path, {"CLAUDE.md": "# notes\n"})
    assert _statuses(tmp_path)["agent.claude_md"] == Status.WARN


def test_invalid_settings_json_warns_instead_of_crashing(tmp_path: Path) -> None:
    bare_project(tmp_path)
    write_project(tmp_path, {".claude/settings.json": "{not json"})
    results = {r.id: r for r in check_agent_readiness(load_context(tmp_path))}
    assert results["agent.plugin"].status == Status.WARN
    assert "not valid JSON" in results["agent.plugin"].evidence
