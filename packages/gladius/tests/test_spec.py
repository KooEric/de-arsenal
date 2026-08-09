"""변환 스펙 검증 — map 선언, map/steps 조합, 필수 존재 규칙 (M3 Task 3.1)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from gladius.spec import TransformSpec


def test_map_only_transform_parses() -> None:
    spec = TransformSpec.model_validate(
        {
            "name": "m",
            "input": "./in",
            "output": "./out",
            "map": {"issue_no": "number", "opened_at": "created_at"},  # new: source
        }
    )
    assert spec.map and spec.steps == []


def test_map_and_steps_compose() -> None:
    # map이 먼저 적용되고 steps가 그 결과 위에서 동작한다
    spec = TransformSpec.model_validate(
        {
            "name": "m",
            "input": "./in",
            "output": "./out",
            "map": {"issue_no": "number"},
            "steps": [{"filter": "issue_no > 10"}],
        }
    )
    assert len(spec.steps) == 1


def test_neither_map_nor_steps_is_invalid() -> None:
    with pytest.raises(ValidationError):
        TransformSpec.model_validate({"name": "m", "input": "./in", "output": "./out"})


def test_output_round_trips_uri_scheme_uncorrupted() -> None:
    """output은 str로 보관 — Path 정규화가 s3:// 같은 URI 스킴을 훼손하면 안 된다.

    pathlib.Path("s3://bucket/x")는 "s3:/bucket/x"로 무너진다 (슬래시 중복 제거).
    P1 httpfs/S3 싱크가 이 필드를 그대로 쓰므로 str로 왕복 보존되어야 한다.
    """
    spec = TransformSpec.model_validate(
        {
            "name": "m",
            "input": "./in",
            "output": "s3://bucket/prefix",
            "map": {"issue_no": "number"},
        }
    )
    assert spec.output == "s3://bucket/prefix"


def test_output_still_accepts_path_object() -> None:
    """Path 객체를 직접 넘기는 기존 호출부(테스트 등)는 그대로 동작해야 한다."""
    spec = TransformSpec.model_validate(
        {"name": "m", "input": "./in", "output": Path("./out"), "map": {"a": "b"}}
    )
    assert spec.output == "out"


def test_sql_escape_hatch_requires_input_placeholder() -> None:
    spec = TransformSpec.model_validate(
        {
            "name": "sql",
            "input": "./in",
            "output": "./out",
            "steps": [{"sql": "SELECT * FROM {input} WHERE amount > 10"}],
        }
    )
    assert spec.steps[0].sql == "SELECT * FROM {input} WHERE amount > 10"  # type: ignore[union-attr]


def test_sql_escape_hatch_rejects_unsafe_shape() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        TransformSpec.model_validate(
            {
                "name": "sql",
                "input": "./in",
                "output": "./out",
                "steps": [{"sql": "SELECT * FROM input"}],
            }
        )


def test_python_udf_step_validates_target_and_types() -> None:
    spec = TransformSpec.model_validate(
        {
            "name": "udf",
            "input": "./in",
            "output": "./out",
            "steps": [
                {
                    "python": "my_transforms:slugify",
                    "args": ["title"],
                    "arg_types": ["VARCHAR"],
                    "output": "slug",
                    "return_type": "VARCHAR",
                }
            ],
        }
    )
    assert spec.steps[0].python == "my_transforms:slugify"  # type: ignore[union-attr]

    with pytest.raises(ValidationError, match="module:function"):
        TransformSpec.model_validate(
            {
                "name": "udf",
                "input": "./in",
                "output": "./out",
                "steps": [{"python": "my_transforms.slugify", "args": ["title"], "output": "slug"}],
            }
        )

    with pytest.raises(ValidationError, match="arg_types"):
        TransformSpec.model_validate(
            {
                "name": "udf",
                "input": "./in",
                "output": "./out",
                "steps": [
                    {
                        "python": "my_transforms:slugify",
                        "args": ["title", "language"],
                        "arg_types": ["VARCHAR"],
                        "output": "slug",
                    }
                ],
            }
        )
    with pytest.raises(ValidationError, match="without"):
        TransformSpec.model_validate(
            {
                "name": "sql",
                "input": "./in",
                "output": "./out",
                "steps": [{"sql": "SELECT * FROM {input}; DROP TABLE x"}],
            }
        )


def test_incremental_modes_and_key_validation() -> None:
    by_unit = TransformSpec.model_validate(
        {
            "name": "inc",
            "input": "./in",
            "output": "./out",
            "incremental": {"mode": "by_unit"},
            "steps": [{"select": ["id"]}],
        }
    )
    assert by_unit.incremental is not None
    assert by_unit.incremental.mode == "by_unit"

    by_key = TransformSpec.model_validate(
        {
            "name": "inc",
            "input": "./in",
            "output": "./out",
            "incremental": {"mode": "by_key", "key": ["id"]},
            "steps": [{"select": ["id"]}],
        }
    )
    assert by_key.incremental is not None
    assert by_key.incremental.key == ["id"]

    with pytest.raises(ValidationError, match="requires"):
        TransformSpec.model_validate(
            {
                "name": "inc",
                "input": "./in",
                "output": "./out",
                "incremental": {"mode": "by_key"},
                "steps": [{"select": ["id"]}],
            }
        )
