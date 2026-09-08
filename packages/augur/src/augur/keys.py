"""API 키 해결 — 환경변수 → 사용자 설정 파일 → 대화형 프롬프트 순.

비밀은 코드·YAML·레포에 두지 않는다(docs/06-conventions.md). 설정 파일은 사용자 홈의
`~/.config/augur/credentials.json`(권한 0600)이며 `AUGUR_HOME`으로 위치를 바꿀 수 있다.
프롬프트는 TTY일 때만 뜬다 — CI나 파이프에서는 조용히 FatalError로 끝나야 한다.
키 값은 어디에도 출력하지 않는다. `augur auth status`는 앞 4자리만 보여준다.
"""

import json
import os
import stat
import sys
import typing as t
from pathlib import Path

import typer

from arsenal_core.errors import FatalError

ENV_VARS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
CREDENTIALS_FILE = "credentials.json"
KEY_PREVIEW_CHARS = 4
OWNER_ONLY = stat.S_IRUSR | stat.S_IWUSR


def config_dir() -> Path:
    override = os.environ.get("AUGUR_HOME")
    if override:
        return Path(override)
    return Path.home() / ".config" / "augur"


def credentials_path() -> Path:
    return config_dir() / CREDENTIALS_FILE


def _env_var(provider: str) -> str:
    try:
        return ENV_VARS[provider]
    except KeyError as e:
        raise FatalError(f"unknown provider {provider!r} (anthropic | openai)") from e


def load_credentials() -> dict[str, str]:
    path = credentials_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise FatalError(f"cannot read credentials file {path}: {e}") from e
    if not isinstance(raw, dict):
        raise FatalError(f"credentials file {path} must be a JSON object")
    data = t.cast(dict[str, t.Any], raw)
    return {str(k): str(v) for k, v in data.items() if v}


def save_credential(provider: str, api_key: str) -> Path:
    """키 하나를 저장. 파일은 소유자만 읽고 쓸 수 있게 만든다."""
    _env_var(provider)
    if not api_key.strip():
        raise FatalError("api key must not be empty")
    creds = load_credentials()
    creds[provider] = api_key.strip()
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(creds, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, OWNER_ONLY)
    return path


def clear_credential(provider: str) -> bool:
    _env_var(provider)
    creds = load_credentials()
    if provider not in creds:
        return False
    del creds[provider]
    path = credentials_path()
    path.write_text(json.dumps(creds, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, OWNER_ONLY)
    return True


def mask(api_key: str) -> str:
    return api_key[:KEY_PREVIEW_CHARS] + "…" if api_key else ""


def key_source(provider: str) -> tuple[str, str] | None:
    """(출처, 키) — 출처는 'env' | 'file'. 없으면 None."""
    var = _env_var(provider)
    value = os.environ.get(var)
    if value:
        return "env", value
    stored = load_credentials().get(provider)
    if stored:
        return "file", stored
    return None


def resolve_api_key(provider: str, *, prompt: bool = True) -> str:
    """CLI 진입점. 못 찾으면 TTY에서만 숨김 입력을 받고, 저장 여부를 묻는다."""
    found = key_source(provider)
    if found is not None:
        return found[1]
    var = _env_var(provider)
    if not prompt or not sys.stdin.isatty():
        raise FatalError(
            f"no API key for {provider}: set {var}, run `augur auth set {provider}`, "
            "or run interactively"
        )
    api_key = str(typer.prompt(f"{provider} API key", hide_input=True)).strip()
    if not api_key:
        raise FatalError("empty API key")
    if typer.confirm(f"save to {credentials_path()}?", default=False):
        save_credential(provider, api_key)
    return api_key
