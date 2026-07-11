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


def _substitute_env(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        value = os.environ.get(m.group(1))
        if value is None:
            raise FatalError(f"environment variable not set: {m.group(1)}")
        return value

    return _ENV_PATTERN.sub(repl, text)


def load_pipeline(path: Path) -> PipelineSpec:
    raw = yaml.safe_load(_substitute_env(path.read_text()))
    try:
        return PipelineSpec.model_validate(raw)
    except ValidationError as e:
        lines = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
        raise FatalError(f"invalid pipeline spec {path}:\n  " + "\n  ".join(lines)) from e
