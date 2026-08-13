"""load_transform 로더 — YAML/인코딩 실패를 FatalError로 번역 (arsenal-core와 동일 계약)."""

import re
from pathlib import Path

import pytest

from arsenal_core.errors import FatalError
from gladius.loader import load_transform

VALID = "name: m\ninput: ./in\noutput: ./out\nmap: { issue_no: number }\n"


def test_broken_yaml_syntax_is_clean_fatal_error(tmp_path: Path) -> None:
    p = tmp_path / "transform.yaml"
    p.write_text(
        "name: m\ninput: ./in\noutput: ./out\nmap: { ts: timestamp[s] }\n", encoding="utf-8"
    )
    with pytest.raises(FatalError) as exc_info:
        load_transform(p)
    message = str(exc_info.value)
    assert "invalid YAML" in message
    assert "transform.yaml" in message
    assert re.search(r"\(line \d+, column \d+\)", message)
    assert "^" not in message


def test_non_utf8_spec_file_is_clean_fatal_error(tmp_path: Path) -> None:
    p = tmp_path / "transform.yaml"
    p.write_bytes(b"name: \xff\xfe not utf-8\n")
    with pytest.raises(FatalError, match="UTF-8"):
        load_transform(p)


def test_utf8_spec_with_non_ascii_content_loads(tmp_path: Path) -> None:
    """플랫폼 기본 인코딩(Windows cp1252)과 무관하게 UTF-8로 읽어야 한다."""
    p = tmp_path / "transform.yaml"
    p.write_bytes((VALID + "# 한글 주석 — em dash\n").encode())
    assert load_transform(p).name == "m"
