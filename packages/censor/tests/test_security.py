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


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("headers: { Authorization: Bearer abc123def }\n", "literal authorization"),
        ('headers: {"X-Api-Key": "abc"}\n', "literal x-api-key"),
        ("options: {api_key: x}\n", "literal api_key"),
        ("users:\n  - password: x\n", "literal password"),
        ("client_secret: abc\n", "literal client_secret"),
        ("access_token: abc\n", "literal access_token"),
        ("api_token: abc\n", "literal api_token"),
        ("passwd: abc\n", "literal passwd"),
        ("url: https://h/x?api_key=abc123\n", "secret in query string"),
        ("key: |\n  -----BEGIN RSA PRIVATE KEY-----\n  MIIB\n", "private key"),
        ('dsn: "host=h password=pw"\n', "password in DSN"),
        ("dsn: postgresql://${U}:literalpw@h/db\n", "credentials in URL"),
        ("note: '-----BEGIN PRIVATE KEY-----'\n", "private key"),
        ("# -----BEGIN OPENSSH PRIVATE KEY-----\n", "private key"),
    ],
)
def test_detects_secret_shapes_the_line_scan_missed(text: str, kind: str) -> None:
    found = find_secrets(text)
    assert [k for _, k in found] == [kind]


def test_secret_line_number_points_at_the_value() -> None:
    text = "name: x\nsource:\n  headers:\n    - {Authorization: Bearer abc}\n"
    assert find_secrets(text) == [(4, "literal authorization")]


def test_block_scalar_secret_reports_the_line_inside_the_block() -> None:
    text = "name: x\nkey: |\n  line one\n  -----BEGIN EC PRIVATE KEY-----\n"
    assert find_secrets(text) == [(4, "private key")]


@pytest.mark.parametrize(
    "text",
    [
        "password: 'true'\n",
        'password: "null"\n',
        "password: 'No'\n",
        'token: ""\n',
        "headers: {Authorization: 'Bearer ${TOKEN}'}\n",
        "url: https://h/x?api_key=${KEY}\n",
        "url: https://h/x?page=2&limit=10\n",
        'dsn: "host=h password=${PG_PASSWORD}"\n',
        "dsn: postgresql://${U}:${P}@h/db\n",
        "users:\n  - password_env: PG_PASSWORD\n",
        "auth: {token_url: 'https://a/token', client_id: abc}\n",
        "keys:\n  - id\n  - password\n",
    ],
)
def test_ignores_quoted_non_secret_scalars_and_env_refs(text: str) -> None:
    assert find_secrets(text) == []


def test_unparseable_yaml_falls_back_to_line_scan() -> None:
    text = "name: [unclosed\npassword: hunter2\n"
    assert find_secrets(text) == [(2, "literal password")]


def test_multiple_secrets_are_sorted_by_line() -> None:
    text = "a: 1\npassword: x\n# AKIAABCDEFGHIJKLMNOP\ntoken: y\n"
    assert find_secrets(text) == [
        (2, "literal password"),
        (3, "AWS access key"),
        (4, "literal token"),
    ]


def test_unreadable_spec_is_na_not_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bare_project(tmp_path)
    real_read_text = Path.read_text

    def flaky(self: Path, encoding: str | None = None) -> str:
        if self.name == "collect.yaml":
            raise PermissionError("permission denied")
        return real_read_text(self, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", flaky)
    results = {r.subject: r for r in check_security(load_context(tmp_path))}
    bad = results["collect.yaml"]
    assert bad.status == Status.NA and not bad.blocker
    assert bad.evidence.startswith("could not read: ")
    assert "permission denied" in bad.evidence
    assert results["transform.yaml"].status == Status.PASS


def test_missing_spec_is_na(tmp_path: Path) -> None:
    bare_project(tmp_path)
    (tmp_path / "transform.yaml").unlink()
    results = {r.subject: r for r in check_security(load_context(tmp_path))}
    assert results["transform.yaml"].status == Status.NA
