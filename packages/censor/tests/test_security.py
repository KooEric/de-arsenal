from pathlib import Path

import pytest
from _projects import bare_project

from censor.checks.security import check_security, find_secrets
from censor.context import load_context
from censor.model import Status

SECRET_TOKEN = "ghp_" + "a" * 36


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("  dsn: postgres://app:hunter2@db:5432/x", "credentials in URL"),
        ("  Authorization: Bearer abcdef123456", "literal authorization"),
        ("  api_key: 'abc123'", "literal api_key"),
        (f"  note: {SECRET_TOKEN}", "GitHub token"),
        ("  key: AKIAABCDEFGHIJKLMNOP", "AWS access key"),
        ("  # AKIAABCDEFGHIJKLMNOP", "AWS access key"),
        (f"  # {SECRET_TOKEN}", "GitHub token"),
        ("  note: sk-" + "a" * 24, "API secret key"),
        ("  api_key: sk-" + "a" * 24, "API secret key"),
        ("  Authorization: Bearer sk-" + "a" * 24, "API secret key"),
    ],
)
def test_detects_literal_secrets(line: str, kind: str) -> None:
    assert find_secrets(f"name: x\n{line}\n") == [(2, kind)]


@pytest.mark.parametrize(
    "line",
    [
        "  dsn_env: PG_DSN",
        "  token_env: GITHUB_TOKEN",
        "  token_url: https://auth.example.com/token",
        "  Authorization: Bearer ${GITHUB_TOKEN}",
        "  url: postgres://app:${PG_PASSWORD}@db/x",
        "  # password: hunter2",
        "  password: ''",
    ],
)
def test_ignores_env_references_comments_and_empty_values(line: str) -> None:
    assert find_secrets(f"{line}\n") == []


@pytest.mark.parametrize(
    "line",
    [
        "  password:",
        "  password: null",
        "  password: Null",
        "  password: NULL",
        "  password: ~",
        "  token: true",
        "  token: False",
        "  secret: yes",
        "  secret: No",
        "  secret: ON",
        "  secret: off",
        "  secret: |",
        "  secret: >",
        "  secret: |-",
        "  secret: >-",
        "  secret: |+",
        "  secret: >+",
        "  password: pre-${VAR}-post",
        "  path: data/sk-learn-aaaaaaaaaaaaaaaaaaaa.csv",
        "  name: my-sk-aaaaaaaaaaaaaaaaaaaaaaaa",
    ],
)
def test_ignores_non_secret_scalars_and_embedded_sk(line: str) -> None:
    assert find_secrets(f"{line}\n") == []


def test_check_security_fails_as_blocker_without_leaking_the_value(tmp_path: Path) -> None:
    bare_project(tmp_path)
    collect = tmp_path / "collect.yaml"
    collect.write_text(collect.read_text() + f"# {SECRET_TOKEN}\nx: {SECRET_TOKEN}\n")
    results = {r.subject: r for r in check_security(load_context(tmp_path))}
    bad = results["collect.yaml"]
    assert bad.status == Status.FAIL and bad.blocker
    assert SECRET_TOKEN not in bad.evidence
    assert bad.evidence.count("GitHub token") == 2
    assert results["transform.yaml"].status == Status.PASS
