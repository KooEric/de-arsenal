"""M2-A 스펙 모델 확장 테스트 — pagination 모드, sink union, auth, validate.

순수 모델 확장(동작 변경 없음)의 하위 호환성을 증명한다: 기존 필드/기본값은
그대로 동작해야 하고, 새 필드는 옵션이어야 한다.
"""

import pytest
from pydantic import TypeAdapter, ValidationError

from arsenal_core.spec.models import (
    AuthSpec,
    PaginationSpec,
    PipelineSpec,
    SinkSpec,
    ValidateRule,
)
from pugio.sources.base import FetchResult


def test_cursor_mode_requires_cursor_fields() -> None:
    with pytest.raises(ValidationError, match="cursor_path"):
        PaginationSpec(mode="cursor", cursor_param="after")


def test_page_mode_defaults() -> None:
    assert PaginationSpec(mode="page").start_page == 1


def test_offset_pagination_unchanged() -> None:
    assert PaginationSpec(mode="offset", size=50).size == 50


def test_fetch_result_next_cursor_defaults_none() -> None:
    assert FetchResult(batch=None, exhausted=True).next_cursor is None


def _minimal_pipeline_dict() -> dict[str, object]:
    return {
        "name": "t",
        "source": {
            "type": "rest",
            "url": "https://x",
            "pagination": {"mode": "offset"},
        },
        "sink": {"type": "parquet", "path": "./out"},
    }


def test_parquet_sink_via_pipeline_still_loads() -> None:
    spec = PipelineSpec.model_validate(_minimal_pipeline_dict())
    assert spec.sink.type == "parquet"


def test_duckdb_sink_discriminates() -> None:
    # TypeAdapter(SinkSpec)의 반환 타입은 Annotated union이라 pyright가 정적으로
    # 좁히지 못한다 (discriminated union을 런타임 값으로 넘길 때의 알려진 한계).
    sink = TypeAdapter(SinkSpec).validate_python(  # pyright: ignore[reportUnknownVariableType]
        {"type": "duckdb", "path": "o.db", "table": "t", "merge_key": ["id"]}
    )
    assert sink.merge_key == ["id"]  # pyright: ignore[reportUnknownMemberType]


def test_s3_uri_path_preserved() -> None:
    from arsenal_core.spec.models import ParquetSinkSpec

    assert ParquetSinkSpec(type="parquet", path="s3://b/x").path == "s3://b/x"


def test_oauth2_auth_spec_defaults() -> None:
    auth = AuthSpec(
        type="oauth2_client_credentials",
        token_url="https://a/t",
        client_id_env="CID",
        client_secret_env="CS",
    )
    assert auth.expiry_buffer_s == 60


def test_validate_rule_shape() -> None:
    assert ValidateRule(field="id", not_null=True, unique=True).min is None


def test_pipeline_validate_alias() -> None:
    raw = _minimal_pipeline_dict()
    raw["validate"] = {"rules": [{"field": "id", "not_null": True}]}
    spec = PipelineSpec.model_validate(raw)
    assert spec.validation is not None
    assert spec.validation.rules[0].field == "id"


def test_pipeline_without_validate_is_none() -> None:
    spec = PipelineSpec.model_validate(_minimal_pipeline_dict())
    assert spec.validation is None
