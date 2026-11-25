"""Can a coding agent operate this pipeline safely?

DE-specific harness checks only; generic harness quality is harness-eval's job.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final, cast

from censor.context import ProjectContext
from censor.model import CheckResult, Dimension, Status

PLUGIN_PREFIX: Final = "de-arsenal@"
REQUIRED_COMMANDS: Final = ("arsenal check", "arsenal run")
_STATE_DIR: Final = ".arsenal"


def _r(
    check_id: str, weight: int, status: Status, subject: str, evidence: str, fix: str
) -> CheckResult:
    return CheckResult(
        id=check_id,
        dimension=Dimension.AGENT_READINESS,
        weight=weight,
        status=status,
        subject=subject,
        evidence=evidence,
        fix=fix if status != Status.PASS else "",
    )


def _settings(root: Path) -> tuple[dict[str, Any], str | None]:
    path = root / ".claude" / "settings.json"
    if not path.is_file():
        return {}, None
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}, ".claude/settings.json is not valid JSON"
    return (cast(dict[str, Any], loaded), None) if isinstance(loaded, dict) else ({}, None)


def _claude_md(root: Path) -> CheckResult:
    path = root / "CLAUDE.md"
    fix = "add a CLAUDE.md that says to run `arsenal check` before and `arsenal run` after edits"
    if not path.is_file():
        return _r("agent.claude_md", 2, Status.FAIL, "CLAUDE.md", "no CLAUDE.md", fix)
    text = path.read_text(encoding="utf-8", errors="replace")
    missing = [c for c in REQUIRED_COMMANDS if c not in text]
    if missing:
        evidence = "CLAUDE.md does not mention " + ", ".join(f"`{c}`" for c in missing)
        return _r("agent.claude_md", 2, Status.WARN, "CLAUDE.md", evidence, fix)
    return _r("agent.claude_md", 2, Status.PASS, "CLAUDE.md", "names arsenal check and run", fix)


def _plugin(settings: dict[str, Any], problem: str | None) -> CheckResult:
    enabled: object = settings.get("enabledPlugins", {})
    names = list(cast(dict[str, object], enabled)) if isinstance(enabled, dict) else []
    ok = any(name.startswith(PLUGIN_PREFIX) for name in names)
    evidence = problem or (
        "de-arsenal plugin enabled for this project"
        if ok
        else "de-arsenal plugin not enabled in .claude/settings.json"
    )
    return _r(
        "agent.plugin",
        1,
        Status.PASS if ok else Status.WARN,
        ".claude/settings.json",
        evidence,
        "/plugin install de-arsenal@de-arsenal, then enable it for the project",
    )


def _deny_rules(settings: dict[str, Any]) -> list[str]:
    permissions: object = settings.get("permissions", {})
    if not isinstance(permissions, dict):
        return []
    deny: object = cast(dict[str, object], permissions).get("deny", [])
    if not isinstance(deny, list):
        return []
    return [r for r in cast(list[object], deny) if isinstance(r, str)]


def _deny_state(settings: dict[str, Any]) -> CheckResult:
    ok = any(_STATE_DIR in rule for rule in _deny_rules(settings))
    return _r(
        "agent.deny_state",
        1,
        Status.PASS if ok else Status.WARN,
        ".claude/settings.json",
        "a deny rule protects .arsenal/" if ok else "nothing stops an agent deleting .arsenal/",
        'add "Bash(rm -rf .arsenal*)" to permissions.deny: .arsenal/ holds run state',
    )


def _secret_hook(root: Path) -> CheckResult:
    hooks = root / ".claude" / "hooks"
    found = False
    for path in sorted(hooks.glob("*")) if hooks.is_dir() else []:
        text = path.read_text(encoding="utf-8", errors="replace").lower() if path.is_file() else ""
        if "secret" in text and ("yaml" in text or "yml" in text):
            found = True
            break
    return _r(
        "agent.secret_hook",
        1,
        Status.PASS if found else Status.WARN,
        ".claude/hooks",
        "a secret-scan hook covers YAML" if found else "no secret-scan hook that covers YAML specs",
        "add a PreToolUse hook that scans *.yaml for secrets before commit",
    )


def check_agent_readiness(ctx: ProjectContext) -> list[CheckResult]:
    settings, problem = _settings(ctx.root)
    return [
        _claude_md(ctx.root),
        _plugin(settings, problem),
        _deny_state(settings),
        _secret_hook(ctx.root),
    ]
