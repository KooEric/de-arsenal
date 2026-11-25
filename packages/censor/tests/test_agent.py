import json
from pathlib import Path
from typing import Any

import pytest
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


def test_disabled_plugin_warns(tmp_path: Path) -> None:
    bare_project(tmp_path)
    write_project(
        tmp_path,
        {".claude/settings.json": json.dumps({"enabledPlugins": {"de-arsenal@de-arsenal": False}})},
    )
    results = {r.id: r for r in check_agent_readiness(load_context(tmp_path))}
    assert results["agent.plugin"].status == Status.WARN
    assert "disabled" in results["agent.plugin"].evidence


def _unreadable(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Make read_text raise OSError for files called `name`.

    Patching is portable: chmod 0o000 is ignored when CI runs as root, and a
    directory or symlink loop fails is_file() before the read is ever attempted.
    """
    original = Path.read_text

    def fake(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == name:
            raise PermissionError(13, "denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake)


def test_unreadable_claude_md_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bare_project(tmp_path)
    write_project(tmp_path, {"CLAUDE.md": CLAUDE_MD})
    _unreadable(monkeypatch, "CLAUDE.md")
    results = {r.id: r for r in check_agent_readiness(load_context(tmp_path))}
    assert results["agent.claude_md"].status == Status.WARN
    assert "CLAUDE.md is unreadable" in results["agent.claude_md"].evidence


def test_unreadable_hook_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bare_project(tmp_path)
    write_project(tmp_path, {".claude/hooks/secret-scan.sh": HOOK})
    _unreadable(monkeypatch, "secret-scan.sh")
    assert _statuses(tmp_path)["agent.secret_hook"] == Status.WARN


def test_unreadable_settings_says_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare_project(tmp_path)
    write_project(tmp_path, {".claude/settings.json": json.dumps(SETTINGS)})
    _unreadable(monkeypatch, "settings.json")
    results = {r.id: r for r in check_agent_readiness(load_context(tmp_path))}
    assert "unreadable" in results["agent.plugin"].evidence
    assert "not valid JSON" not in results["agent.plugin"].evidence
