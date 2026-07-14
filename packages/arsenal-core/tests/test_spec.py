from pathlib import Path

import pytest

from arsenal_core.errors import FatalError
from arsenal_core.spec import load_pipeline

VALID = """
name: github-issues
source:
  type: rest
  url: https://api.example.com/items
  headers: { Authorization: "Bearer ${TEST_TOKEN}" }
  pagination: { mode: offset, param: offset, size_param: limit, size: 100 }
sink:
  type: parquet
  path: ./data/items
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "pipe.yaml"
    p.write_text(text)
    return p


def test_valid_spec_parses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok123")
    spec = load_pipeline(write(tmp_path, VALID))
    assert spec.name == "github-issues"
    source = spec.source
    assert source.type == "rest"  # discriminator로 좁혀야 REST 전용 필드에 접근 가능
    assert source.pagination.size == 100
    assert source.headers["Authorization"] == "Bearer tok123"  # env 치환


def test_missing_required_field_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="source.url"):
        load_pipeline(
            write(tmp_path, "name: x\nsource: {type: rest}\nsink: {type: parquet, path: d}")
        )


def test_unknown_field_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TOKEN", "tok123")
    with pytest.raises(FatalError, match="tyop"):
        load_pipeline(write(tmp_path, VALID.replace("type: parquet", "type: parquet\n  tyop: 1")))


def test_missing_env_var_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_TOKEN", raising=False)
    with pytest.raises(FatalError, match="TEST_TOKEN"):
        load_pipeline(write(tmp_path, VALID))


def test_unknown_source_type_names_source_and_lists_expected_tags(tmp_path: Path) -> None:
    """discriminator 값이 태그 목록에 없으면 source에 앵커된 친절한 에러(_clean_loc 경로)."""
    with pytest.raises(FatalError) as exc_info:
        load_pipeline(
            write(tmp_path, "name: x\nsource: {type: bogus}\nsink: {type: parquet, path: d}")
        )
    message = str(exc_info.value)
    assert "source:" in message
    for tag in ("rest", "file", "database", "python"):
        assert tag in message


def test_source_without_type_reports_discriminator_error_at_source(tmp_path: Path) -> None:
    """type 키 자체가 없으면 discriminator를 못 뽑는다는 에러가 source에 앵커된다."""
    with pytest.raises(FatalError, match="source: Unable to extract tag using discriminator"):
        load_pipeline(
            write(tmp_path, "name: x\nsource: {url: https://x}\nsink: {type: parquet, path: d}")
        )
