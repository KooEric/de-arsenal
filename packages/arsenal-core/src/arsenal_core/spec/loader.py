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
        text = path.read_text()
    except FileNotFoundError as e:
        raise FatalError(f"spec file not found: {path}") from e
    except OSError as e:
        raise FatalError(f"cannot read spec file {path}: {e}") from e

    raw = yaml.safe_load(_substitute_env(text))
    try:
        return PipelineSpec.model_validate(raw)
    except ValidationError as e:
        lines = [
            f"{'.'.join(str(p) for p in _clean_loc(err['loc']))}: {err['msg']}"
            for err in e.errors()
        ]
        raise FatalError(f"invalid pipeline spec {path}:\n  " + "\n  ".join(lines)) from e
