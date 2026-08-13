"""YAML → 검증된 PipelineSpec. 에러 메시지 품질이 곧 UX.

구현: docs/plans/2026-07-08-m1-core-foundation.md Task 4
- ${VAR} 환경변수 치환 (비밀은 YAML에 두지 않는다)
- 검증 실패 시 필드 경로가 담긴 FatalError
"""

import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import PipelineSpec

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")

# discriminated union 필드 → 허용된 태그 값. Pydantic은 이런 필드의 에러 loc에
# 태그를 끼워 넣는다(예: ("source","rest","url")) — 사용자에게는 YAML 그대로의
# 경로(source.url)가 더 읽기 쉽고, M1 에러 메시지와도 하위 호환된다.
_UNION_TAGS: dict[str, frozenset[str]] = {
    "source": frozenset({"rest", "file", "database", "python"}),
    "sink": frozenset({"parquet", "duckdb", "postgres"}),
}


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


def _clean_loc(loc: tuple[int | str, ...]) -> tuple[int | str, ...]:
    """discriminated union의 태그 세그먼트를 제거한다 (source.rest.url → source.url)."""
    cleaned: list[int | str] = []
    for i, part in enumerate(loc):
        prev = loc[i - 1] if i > 0 else None
        allowed = _UNION_TAGS.get(str(prev)) if isinstance(prev, str) else None
        if allowed is not None and isinstance(part, str) and part in allowed:
            continue
        cleaned.append(part)
    return tuple(cleaned)


def load_pipeline(path: Path) -> PipelineSpec:
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
        return PipelineSpec.model_validate(raw)
    except ValidationError as e:
        lines = [
            f"{'.'.join(str(p) for p in _clean_loc(err['loc']))}: {err['msg']}"
            for err in e.errors()
        ]
        raise FatalError(f"invalid pipeline spec {path}:\n  " + "\n  ".join(lines)) from e
