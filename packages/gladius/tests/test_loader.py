"""load_transform 로더 — YAML 문법 오류를 FatalError로 번역 (arsenal-core 로더와 동일 계약)."""

from pathlib import Path

import pytest

from arsenal_core.errors import FatalError
from gladius.loader import load_transform


def test_broken_yaml_syntax_is_clean_fatal_error(tmp_path: Path) -> None:
    """YAML 문법 오류(예: 플로우 매핑 안의 따옴표 없는 timestamp[s])는 원시 트레이스백이
    아니라 파일 경로와 문제 위치가 담긴 FatalError로 번역되어야 한다."""
    broken = "name: m\ninput: ./in\noutput: ./out\nmap: { ts: timestamp[s] }\n"
    p = tmp_path / "transform.yaml"
    p.write_text(broken)
    with pytest.raises(FatalError) as exc_info:
        load_transform(p)
    message = str(exc_info.value)
    assert "invalid YAML" in message
    assert "transform.yaml" in message
    assert "while parsing a flow mapping" in message  # context
    assert "line 4" in message  # 문제 위치 요약 (map 줄)
