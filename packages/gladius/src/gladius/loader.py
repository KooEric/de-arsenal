"""YAML → 검증된 TransformSpec (M3 Task 3.5). arsenal-core 로더 패턴 재사용.

- ${VAR} 환경변수 치환 (비밀은 YAML에 두지 않는다)
- 검증 실패 시 필드 경로가 담긴 FatalError
"""

import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from arsenal_core.errors import FatalError
from gladius.spec import TransformSpec

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _substitute_env(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        value = os.environ.get(m.group(1))
        if value is None:
            raise FatalError(f"environment variable not set: {m.group(1)}")
        return value

    return _ENV_PATTERN.sub(repl, text)


def _yaml_error_summary(e: yaml.YAMLError) -> str:
    """YAMLError를 한 줄 요약으로: 문제 설명 + (line, column). 마크가 없으면 str(e)."""
    if isinstance(e, yaml.MarkedYAMLError) and e.problem is not None:
        mark = e.problem_mark
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        context = f"{e.context}: " if e.context else ""
        return f"{context}{e.problem}{where}"
    return str(e)


def load_transform(path: Path) -> TransformSpec:
    try:
        # YAML 규격은 UTF-8 — 플랫폼 기본 인코딩(Windows cp1252)에 맡기면 안 된다
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise FatalError(f"spec file not found: {path}") from e
    except UnicodeDecodeError as e:
        raise FatalError(f"spec file {path} is not valid UTF-8: {e}") from e
    except OSError as e:
        raise FatalError(f"cannot read spec file {path}: {e}") from e

    try:
        raw = yaml.safe_load(_substitute_env(text))
    except yaml.YAMLError as e:
        raise FatalError(f"invalid YAML in {path}: {_yaml_error_summary(e)}") from e
    try:
        return TransformSpec.model_validate(raw)
    except ValidationError as e:
        lines = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
        raise FatalError(f"invalid transform spec {path}:\n  " + "\n  ".join(lines)) from e
