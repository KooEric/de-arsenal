"""load_transform 로더 — YAML 문법 오류를 FatalError로 번역 (arsenal-core 로더와 동일 계약)."""

from pathlib import Path

import pytest

from arsenal_core.errors import FatalError
from gladius.loader import load_transform

VALID = "name: m\ninput: ./in\noutput: ./out\nmap: { issue_no: number }\n"


def test_non_utf8_spec_file_is_clean_fatal_error(tmp_path: Path) -> None:
    """YAML은 UTF-8이 규격 — 디코딩 실패도 원시 UnicodeDecodeError가 아니라 FatalError."""
    p = tmp_path / "transform.yaml"
    p.write_bytes(b"name: \xff\xfe not utf-8\n")
    with pytest.raises(FatalError) as exc_info:
        load_transform(p)
    message = str(exc_info.value)
    assert "UTF-8" in message
    assert "transform.yaml" in message


def test_utf8_spec_with_non_ascii_content_loads(tmp_path: Path) -> None:
    """플랫폼 기본 인코딩(Windows cp1252)과 무관하게 UTF-8로 읽어야 한다."""
    p = tmp_path / "transform.yaml"
    p.write_bytes((VALID + "# 한글 주석 — em dash\n").encode())
    assert load_transform(p).name == "m"


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
