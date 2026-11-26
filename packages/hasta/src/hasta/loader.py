"""YAML loader for the dedicated Hasta stream specification."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from arsenal_core.errors import FatalError, MissingEnvError
from hasta.spec import StreamSpec

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _substitute_env(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        value = os.environ.get(match.group(1))
        if value is None:
            raise MissingEnvError(match.group(1))
        return value

    return _ENV_PATTERN.sub(replace, text)


def load_stream(path: Path) -> StreamSpec:
    try:
        raw = yaml.safe_load(_substitute_env(path.read_text(encoding="utf-8")))
    except FileNotFoundError as error:
        raise FatalError(f"stream spec file not found: {path}") from error
    except OSError as error:
        raise FatalError(f"cannot read stream spec {path}: {error}") from error
    try:
        return StreamSpec.model_validate(raw)
    except ValidationError as error:
        lines = [
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        ]
        raise FatalError(f"invalid stream spec {path}:\n  " + "\n  ".join(lines)) from error
