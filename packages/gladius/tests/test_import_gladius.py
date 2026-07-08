"""스켈레톤 배선 검증 — 공개 API가 전부 import 가능하고 이름이 살아 있어야 한다."""

import gladius
from gladius.cli import app
from gladius.compile import compile_sql
from gladius.engine import run_transform
from gladius.spec import (
    CastStep,
    DedupStep,
    DeriveStep,
    FilterStep,
    RenameStep,
    SelectStep,
    TransformSpec,
)


def test_version() -> None:
    assert gladius.__version__


def test_public_api_is_wired() -> None:
    assert callable(compile_sql)
    assert callable(run_transform)
    assert app.info is not None  # typer app 존재
    wired = (CastStep, DedupStep, DeriveStep, FilterStep, RenameStep, SelectStep, TransformSpec)
    assert all(cls is not None for cls in wired)


def test_transform_spec_parses_example_shape() -> None:
    """docs/00-overview.md 시나리오 D의 선언이 모델로 파싱되는지 — 스펙이 곧 방향."""
    spec = TransformSpec.model_validate(
        {
            "name": "clean-issues",
            "input": "./data/issues",
            "steps": [
                {"filter": "state = 'open'"},
                {"rename": {"created_at": "opened_at"}},
                {"cast": {"number": "bigint"}},
                {"select": ["number", "title", "opened_at", "user"]},
            ],
            "output": "./data/issues_clean",
        }
    )
    assert len(spec.steps) == 4
    assert isinstance(spec.steps[0], FilterStep)
