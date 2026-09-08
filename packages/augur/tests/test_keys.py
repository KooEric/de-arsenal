"""키 해결 순서(env → file → prompt)와 auth 커맨드. 키 값은 출력에 나오면 안 된다."""

import json
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arsenal_core.errors import FatalError
from augur.cli import app
from augur.keys import credentials_path, key_source, resolve_api_key, save_credential

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AUGUR_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return tmp_path / "cfg"


def test_env_wins_over_file(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_credential("anthropic", "sk-file")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert key_source("anthropic") == ("env", "sk-env")
    assert resolve_api_key("anthropic", prompt=False) == "sk-env"


def test_file_used_when_env_missing_and_is_owner_only(home: Path) -> None:
    path = save_credential("openai", "  sk-file  ")
    assert path == credentials_path() and path.parent == home
    assert json.loads(path.read_text(encoding="utf-8")) == {"openai": "sk-file"}
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert resolve_api_key("openai", prompt=False) == "sk-file"


def test_missing_key_without_tty_is_fatal(home: Path) -> None:
    with pytest.raises(FatalError, match="augur auth set anthropic"):
        resolve_api_key("anthropic", prompt=False)
    with pytest.raises(FatalError, match="unknown provider"):
        resolve_api_key("gemini")


def test_auth_set_status_clear_never_print_the_key(home: Path) -> None:
    result = runner.invoke(app, ["auth", "set", "anthropic"], input="sk-ant-secret-value\n")
    assert result.exit_code == 0, result.output
    assert "saved anthropic key" in result.output and "secret" not in result.output
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    assert "anthropic  file  sk-a…" in result.output and "secret" not in result.output
    assert "openai     none" in result.output
    result = runner.invoke(app, ["auth", "clear", "anthropic"])
    assert result.exit_code == 0 and "cleared anthropic" in result.output
    assert key_source("anthropic") is None
    result = runner.invoke(app, ["auth", "clear", "anthropic"])
    assert "nothing stored" in result.output


def test_auth_set_rejects_empty_and_unknown(home: Path) -> None:
    assert runner.invoke(app, ["auth", "set", "anthropic"], input="   \n").exit_code == 1
    assert runner.invoke(app, ["auth", "set", "gemini"], input="x\n").exit_code == 1


def test_interactive_prompt_can_save(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "sk-typed")  # type: ignore[misc]
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)  # type: ignore[misc]
    assert resolve_api_key("anthropic") == "sk-typed"
    assert key_source("anthropic") == ("file", "sk-typed")
