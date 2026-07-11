"""변환 스펙 검증 — map 선언, map/steps 조합, 필수 존재 규칙 (M3 Task 3.1)."""

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
