"""Regression: the shipped github-issues recipe must collect every page.

The recipe used to say ``mode: offset, param: page``. Offset mode sends
0, size, 2*size, … so against GitHub's page-numbered API it requested page=0
(GitHub serves page 1) and then page=100, which is empty — everything after the
first page was dropped and the run still reported success. Dogfooding against
the real API collected 6 of 21 issues with per_page=5.

The mock below follows GitHub's page semantics (page < 1 is page 1).
"""

from importlib import resources
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest
import respx

from arsenal_core.spec.loader import load_pipeline
from arsenal_core.spec.models import ParquetSinkSpec
from pugio.runner import run_pipeline

_TOTAL_ISSUES = 250


def _github_page_numbered(request: httpx.Request) -> httpx.Response:
    params = dict(request.url.params)
    page = max(int(params.get("page", "1")), 1)
    per_page = int(params.get("per_page", "30"))
    start = (page - 1) * per_page
    issues = [{"number": n} for n in range(start + 1, min(start + per_page, _TOTAL_ISSUES) + 1)]
    return httpx.Response(200, json=issues)


@respx.mock
def test_github_issues_recipe_collects_every_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    recipe = resources.files("arsenal") / "recipes" / "github-issues" / "collect.yaml"
    spec_path = tmp_path / "collect.yaml"
    spec_path.write_text(recipe.read_text(encoding="utf-8"), encoding="utf-8")
    spec = load_pipeline(spec_path)
    out = tmp_path / "issues"
    spec = spec.model_copy(
        update={
            "state_dir": str(tmp_path / ".arsenal"),
            "sink": ParquetSinkSpec(type="parquet", path=str(out)),
        }
    )
    respx.get(url__startswith="https://api.github.com/").mock(side_effect=_github_page_numbered)

    run_pipeline(spec)

    numbers: list[int] = []
    for f in sorted(out.glob("*.parquet")):
        numbers.extend(pq.read_table(f).column("number").to_pylist())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    assert sorted(numbers) == list(range(1, _TOTAL_ISSUES + 1))
