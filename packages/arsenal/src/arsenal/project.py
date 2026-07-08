"""arsenal.yaml — 프로젝트 매니페스트. `arsenal run`의 실행 순서 선언.

구현: docs/plans/2026-07-08-m4-integration-release.md Task 4.0
경로는 매니페스트 파일 기준 상대 경로로 해석한다.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class ArsenalProject(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    pipelines: list[Path] = []  # pugio 스펙들 — 순서대로 실행
    transforms: list[Path] = []  # gladius 스펙들 — pipelines 완료 후 순서대로


def load_project(manifest_path: Path) -> ArsenalProject:
    """arsenal.yaml 로드 + 상대 경로를 매니페스트 기준 절대 경로로 해석."""
    raise NotImplementedError("M4 Task 4.0 — docs/plans/2026-07-08-m4-integration-release.md")
