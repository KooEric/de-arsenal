"""YAML → 검증된 PipelineSpec. 에러 메시지 품질이 곧 UX.

구현: docs/plans/2026-07-08-m1-core-foundation.md Task 4
- ${VAR} 환경변수 치환 (비밀은 YAML에 두지 않는다)
- 검증 실패 시 필드 경로가 담긴 FatalError
"""

from pathlib import Path

from arsenal_core.spec.models import PipelineSpec


def load_pipeline(path: Path) -> PipelineSpec:
    raise NotImplementedError("M1 Task 4 — docs/plans/2026-07-08-m1-core-foundation.md")
