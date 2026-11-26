"""Secrets belong in environment variables (`*_env`, `${VAR}`), never in spec text."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Final, cast

import yaml

from censor.context import ProjectContext, SpecFile
from censor.model import CheckResult, Dimension, Status
from censor.result import make_result

_ENV_REF: Final = "${"
_TOKEN_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("API secret key", re.compile(r"(?<![\w/.-])sk-[A-Za-z0-9_-]{20,}\b")),
)
_URL_CREDENTIALS: Final = re.compile(
    r"[a-z][a-z0-9+.-]*://[^\s/:@]*:(?P<password>[^\s/@]+)@", re.IGNORECASE
)
_PRIVATE_KEY: Final = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")
_QUERY_SECRET: Final = re.compile(
    r"[?&](?:api_key|apikey|access_token|token|key)=(?P<value>[^&\s#]+)", re.IGNORECASE
)
_DSN_PASSWORD: Final = re.compile(r"(?:^|\s)password=(?P<value>'[^']*'|[^\s'\"]+)", re.IGNORECASE)
# Matches the whole key: `client_secret`, `x-api-key`; never `token_env` or `token_url`.
_SECRET_KEY_NAME: Final = re.compile(
    r"(?:^|[_-])(?:authorization|api[_-]?key|token|access[_-]?token|password|passwd|pwd"
    r"|secret|client[_-]?secret|private[_-]?key)$",
    re.IGNORECASE,
)
# Line-scan fallback for text that is not YAML.
_SECRET_KEY_LINE: Final = re.compile(
    r"^\s*[\"']?(authorization|x-api-key|api[_-]?key|token|password|secret)[\"']?\s*:\s*(.*)$",
    re.IGNORECASE,
)
_NON_SECRET_SCALARS: Final = frozenset(
    {"null", "~", "true", "false", "yes", "no", "on", "off", "|", ">", "|-", ">-", "|+", ">+"}
)
_FIX: Final = (
    "move the value to an environment variable (`dsn_env`, `token_env`, `${VAR}`) and rotate it"
)


def _literal_scalar(value: str) -> bool:
    value = value.strip().strip("\"'").strip()
    return bool(value) and _ENV_REF not in value and value.lower() not in _NON_SECRET_SCALARS


def _literal_value(value: str) -> bool:
    return _literal_scalar(value.split(" #", 1)[0])


def _pattern_secret(text: str) -> str | None:
    """Kinds that are recognisable anywhere: in a value, a comment or a non-YAML line."""
    for kind, pattern in _TOKEN_PATTERNS:
        if pattern.search(text):
            return kind
    url = _URL_CREDENTIALS.search(text)
    if url and _ENV_REF not in url.group("password"):
        return "credentials in URL"
    if _PRIVATE_KEY.search(text):
        return "private key"
    return None


def _value_secret(value: str) -> str | None:
    kind = _pattern_secret(value)
    if kind:
        return kind
    query = _QUERY_SECRET.search(value)
    if query and _literal_scalar(query.group("value")):
        return "secret in query string"
    dsn = _DSN_PASSWORD.search(value)
    if dsn and _literal_scalar(dsn.group("value")):
        return "password in DSN"
    return None


def _line_secret(line: str) -> str | None:
    kind = _pattern_secret(line)
    if kind or line.lstrip().startswith("#"):
        return kind  # only the key-name heuristic skips comments: `# password: x` is prose
    key = _SECRET_KEY_LINE.match(line)
    if key and _literal_value(key.group(2)):
        return f"literal {key.group(1).lower()}"
    return None


def _scan_lines(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        kind = _line_secret(line)
        if kind:
            found.append((number, kind))
    return found


def _scalar_line(node: yaml.ScalarNode, kind: str) -> int:
    line = node.start_mark.line + 1
    if node.style != "|":
        return line
    for offset, text in enumerate(node.value.splitlines(), start=1):
        if _value_secret(text) == kind:
            return line + offset  # a literal block keeps its lines: report the one that leaked
    return line


class _Walker:
    def __init__(self) -> None:
        self.found: set[tuple[int, str]] = set()
        self._seen: set[int] = set()

    def walk(self, node: yaml.Node) -> None:
        if id(node) in self._seen:  # aliases can make the graph cyclic
            return
        self._seen.add(id(node))
        if isinstance(node, yaml.ScalarNode):
            kind = _value_secret(node.value)
            if kind:
                self.found.add((_scalar_line(node, kind), kind))
        elif isinstance(node, yaml.SequenceNode):
            for child in cast(list[yaml.Node], node.value):
                self.walk(child)
        elif isinstance(node, yaml.MappingNode):
            for key, value in cast(list[tuple[yaml.Node, yaml.Node]], node.value):
                self._pair(key, value)

    def _pair(self, key: yaml.Node, value: yaml.Node) -> None:
        self.walk(key)
        self.walk(value)
        if not (isinstance(key, yaml.ScalarNode) and isinstance(value, yaml.ScalarNode)):
            return
        if not _SECRET_KEY_NAME.search(key.value) or _value_secret(value.value):
            return  # a recognised value pattern already reported this pair
        if _literal_scalar(value.value):
            self.found.add((key.start_mark.line + 1, f"literal {key.value.lower()}"))


def find_secrets(text: str) -> list[tuple[int, str]]:
    """Return (1-based line, kind) for each literal secret. Never returns the value."""
    try:
        composed = cast(
            Iterator[yaml.Node | None],
            yaml.compose_all(text, Loader=yaml.SafeLoader),  # pyright: ignore[reportUnknownMemberType]
        )
        documents = list(composed)
    except yaml.YAMLError:
        return _scan_lines(text)
    walker = _Walker()
    for document in documents:
        if document is not None:
            walker.walk(document)
    for number, line in enumerate(text.splitlines(), start=1):
        kind = _pattern_secret(line)
        if kind:
            walker.found.add((number, kind))
    return sorted(walker.found)


def _check_file(spec: SpecFile) -> CheckResult:
    check_id = f"security.secrets:{spec.name}"
    if spec.read_error is not None:
        evidence = f"could not read: {spec.read_error}"
        return make_result(check_id, Dimension.SECURITY, 5, Status.NA, spec.name, evidence)
    found = find_secrets(spec.text)
    evidence = (
        "; ".join(f"line {n}: {kind}" for n, kind in found) if found else "no literal secrets"
    )
    status = Status.FAIL if found else Status.PASS
    return make_result(
        check_id, Dimension.SECURITY, 5, status, spec.name, evidence, _FIX, blocker=bool(found)
    )


def check_security(ctx: ProjectContext) -> list[CheckResult]:
    return [_check_file(s) for s in (*ctx.pipelines, *ctx.transform_files)]
