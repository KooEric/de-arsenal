"""M2-H: 모든 예제 YAML이 load_pipeline()으로 파싱/검증까지 통과해야 한다 —
문서가 아니라 실제로 유효한 파이프라인 스펙이라는 걸 증명한다 (실행은 하지
않는다 — 계정/네트워크 불필요).
"""

import re
from pathlib import Path

import pytest

from arsenal_core.spec.loader import load_pipeline

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")
_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLES_DIR = _REPO_ROOT / "examples"

# transform.yaml은 pugio PipelineSpec이 아니라 *gladius* 스펙(input/steps/output)이다
# — load_pipeline()은 extra="forbid"라 이 스키마를 의도적으로 거부한다. 여기서
# 제외하는 건 "숨기는" 게 아니라 애초에 다른 도구의 파일이기 때문이다.
_NOT_PUGIO_SPECS = {"transform.yaml"}


def _example_yaml_paths() -> list[Path]:
    top_level = [p for p in sorted(_EXAMPLES_DIR.glob("*.yaml")) if p.name not in _NOT_PUGIO_SPECS]
    real_world = sorted((_EXAMPLES_DIR / "real-world").glob("*.yaml"))
    return top_level + real_world


def _env_vars_referenced(text: str) -> set[str]:
    return set(_ENV_PATTERN.findall(text))


_PATHS = _example_yaml_paths()
assert _PATHS, f"no example YAMLs found under {_EXAMPLES_DIR} — glob is broken"


@pytest.mark.parametrize(
    "path", _PATHS, ids=[p.relative_to(_EXAMPLES_DIR).as_posix() for p in _PATHS]
)
def test_example_yaml_loads_without_error(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """${VAR} 치환이 필요한 예제(예: github-issues.yaml, data-go-kr.yaml)는 더미
    값으로 채워 로더의 파싱/검증 자체만 확인한다 — load_pipeline은 스펙을 만들
    뿐 실행하지 않으므로 실제 시크릿은 필요 없다."""
    for var in _env_vars_referenced(path.read_text(encoding="utf-8")):
        monkeypatch.setenv(var, "dummy-value-for-load-test")
    spec = load_pipeline(path)
    assert spec.name  # 최소 정합성 — 이름 있는 유효한 PipelineSpec이 나왔다
