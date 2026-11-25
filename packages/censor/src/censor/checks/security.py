"""Secrets belong in environment variables (`*_env`, `${VAR}`), never in spec text."""

from __future__ import annotations

import re
from typing import Final

from censor.context import ProjectContext, SpecFile
from censor.model import CheckResult, Dimension, Status

_ENV_REF: Final = "${"
_TOKEN_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("API secret key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
)
_URL_CREDENTIALS: Final = re.compile(r"[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE)
_SECRET_KEY: Final = re.compile(
    r"^\s*[\"']?(authorization|x-api-key|api[_-]?key|token|password|secret)[\"']?\s*:\s*(.*)$",
    re.IGNORECASE,
)
_FIX: Final = (
    "move the value to an environment variable (`dsn_env`, `token_env`, `${VAR}`) and rotate it"
)


def _literal_value(value: str) -> bool:
    value = value.split(" #", 1)[0].strip().strip("\"'").strip()
    return bool(value) and _ENV_REF not in value


def _line_secret(line: str) -> str | None:
    if line.lstrip().startswith("#"):
        return None
    for kind, pattern in _TOKEN_PATTERNS:
        if pattern.search(line):
            return kind
    match = _URL_CREDENTIALS.search(line)
    if match and _ENV_REF not in match.group(0):
        return "credentials in URL"
    key = _SECRET_KEY.match(line)
    if key and _literal_value(key.group(2)):
        return f"literal {key.group(1).lower()}"
    return None


def find_secrets(text: str) -> list[tuple[int, str]]:
    """Return (1-based line, kind) for each literal secret. Never returns the value."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        kind = _line_secret(line)
        if kind:
            found.append((number, kind))
    return found


def _check_file(spec: SpecFile) -> CheckResult:
    found = find_secrets(spec.text)
    evidence = (
        "; ".join(f"line {n}: {kind}" for n, kind in found) if found else "no literal secrets"
    )
    return CheckResult(
        id=f"security.secrets:{spec.name}",
        dimension=Dimension.SECURITY,
        weight=5,
        status=Status.FAIL if found else Status.PASS,
        blocker=bool(found),
        subject=spec.name,
        evidence=evidence,
        fix=_FIX if found else "",
    )


def check_security(ctx: ProjectContext) -> list[CheckResult]:
    return [_check_file(s) for s in (*ctx.pipelines, *ctx.transform_files)]
