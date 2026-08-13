"""arsenal.yaml — 프로젝트 매니페스트. `arsenal run`의 실행 순서 선언.

구현: docs/plans/2026-07-08-m4-integration-release.md Task 4.0
경로는 매니페스트 파일 기준 상대 경로로 해석한다.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from arsenal_core.errors import FatalError
from arsenal_core.yaml_io import yaml_error_summary


class ArsenalProject(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    pipelines: list[Path] = []  # pugio 스펙들 — 순서대로 실행
    transforms: list[Path] = []  # gladius 스펙들 — pipelines 완료 후 순서대로


def load_project(manifest_path: Path) -> ArsenalProject:
    """arsenal.yaml 로드 + 상대 경로를 매니페스트 기준 절대 경로로 해석.

    참조된 파이프라인/변환 스펙 파일의 실존 여부는 검사하지 않는다 — 경로
    해석만 한다 (존재 검사는 각 스펙을 실제로 로드하는 시점의 책임).
    """
    try:
        # YAML 규격은 UTF-8 — 플랫폼 기본 인코딩(Windows cp1252)에 맡기면 안 된다
        text = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise FatalError(f"manifest not found: {manifest_path}") from e
    except UnicodeDecodeError as e:
        raise FatalError(f"manifest {manifest_path} is not valid UTF-8: {e}") from e
    except OSError as e:
        raise FatalError(f"cannot read manifest {manifest_path}: {e}") from e

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise FatalError(f"invalid YAML in {manifest_path}: {yaml_error_summary(e)}") from e

    try:
        parsed = ArsenalProject.model_validate(raw)
    except ValidationError as e:
        lines = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
        raise FatalError(f"invalid manifest {manifest_path}:\n  " + "\n  ".join(lines)) from e

    base = manifest_path.parent
    if not base.is_absolute():
        base = base.absolute()

    return ArsenalProject(
        name=parsed.name,
        pipelines=[base / p for p in parsed.pipelines],
        transforms=[base / t for t in parsed.transforms],
    )
