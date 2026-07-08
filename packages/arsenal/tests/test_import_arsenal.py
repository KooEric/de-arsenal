"""스켈레톤 배선 검증 — 공개 API가 전부 import 가능하고 이름이 살아 있어야 한다."""

from importlib import resources

import arsenal
from arsenal.cli import app
from arsenal.project import ArsenalProject, load_project


def test_version() -> None:
    assert arsenal.__version__


def test_public_api_is_wired() -> None:
    assert callable(load_project)
    assert app.info is not None
    assert ArsenalProject is not None


def test_recipe_shape_example_ships_with_package() -> None:
    """레시피는 패키지 데이터다 — github-issues가 형태 예시로 포함되어야 한다."""
    recipe = resources.files("arsenal") / "recipes" / "github-issues"
    names = {p.name for p in recipe.iterdir()}
    assert {"arsenal.yaml", "collect.yaml", "transform.yaml", "README.md"} <= names
