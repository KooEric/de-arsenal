from pathlib import Path

import pytest

from arsenal_core.errors import FatalError
from arsenal_core.spec import ParquetSinkSpec, load_pipeline

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


def test_sink_spec_path_round_trips_uri_scheme_uncorrupted() -> None:
    """SinkSpec.path는 str로 보관 — Path 정규화가 s3:// 같은 URI 스킴을 훼손하면 안 된다.

    pathlib.Path("s3://bucket/x")는 "s3:/bucket/x"로 무너진다 (슬래시 중복 제거).
    P1 httpfs/S3 싱크가 이 필드를 그대로 쓰므로 str로 왕복 보존되어야 한다.
    """
    spec = ParquetSinkSpec(type="parquet", path="s3://bucket/prefix")
    assert spec.path == "s3://bucket/prefix"


def test_sink_spec_path_still_accepts_path_object(tmp_path: Path) -> None:
    """로컬 경로를 Path로 넘기는 기존 호출부(예: YAML 로더 경유)는 그대로 동작해야 한다.

    model_validate로 검증한다 — 직접 키워드 생성자 호출은 pydantic이 pyright용으로
    합성하는 __init__ 시그니처가 선언 타입(str) 그대로라 Path를 거부하지만(정적），
    YAML/딕셔너리 경유(model_validate)는 이 before-validator가 실제로 담당하는 실행
    경로다.
    """
    spec = ParquetSinkSpec.model_validate({"type": "parquet", "path": tmp_path / "out"})
    assert spec.path == str(tmp_path / "out")
