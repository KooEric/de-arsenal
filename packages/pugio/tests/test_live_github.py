"""M2-H: 실 API E2E — opt-in. 계정/네트워크가 있을 때만 돈다.

RUN_LIVE=1이 아니면 무조건 스킵된다 (CI/기본 실행 경로에는 토큰이 전혀
필요 없다 — `uv run pytest`를 그냥 돌리면 이 파일의 테스트는 SKIPPED로
보고되지, FAILED가 아니다).

실행: RUN_LIVE=1 GITHUB_TOKEN=ghp_xxx uv run pytest packages/pugio/tests/test_live_github.py

respx 계약 테스트(test_real_world_apis.py)가 이미 GitHub의 Link 헤더 계약과
static_token auth 배선을 목으로 검증했다 — 여기는 그 계약이 실제 GitHub API에도
성립하는지 최종 확인용이다. 페이지네이션을 끝까지(수천 페이지) 돌리면 API
쿼터를 낭비하므로, 크래시 주입으로 정확히 3페이지에서 멈추도록 통제한다.
"""

import os
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from arsenal_core.spec.models import (
    AuthSpec,
    PaginationSpec,
    ParquetSinkSpec,
    PipelineSpec,
    RestSourceSpec,
)
from pugio.runner import run_pipeline

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1",
    reason="live network test — set RUN_LIVE=1 (and GITHUB_TOKEN) to run against real GitHub",
)


class _StopAfterNPages(Exception):
    """실제 크래시가 아니라 "정확히 N페이지에서 멈춰라"는 테스트 통제 신호 —
    GitHub의 전체 이슈 목록(수천 페이지)을 끝까지 도는 걸 막는다."""


def _spec(tmp_path: Path) -> PipelineSpec:
    return PipelineSpec(
        name="live-github",
        state_dir=tmp_path / ".arsenal",
        source=RestSourceSpec(
            type="rest",
            url="https://api.github.com/repos/duckdb/duckdb/issues",
            pagination=PaginationSpec(mode="link", size_param="per_page", size=10),
            auth=AuthSpec(type="static_token", token_env="GITHUB_TOKEN"),
        ),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )


def test_live_github_three_pages_with_crash_and_resume(tmp_path: Path) -> None:
    """실 GitHub API: link 모드로 3페이지 수집 + 중단 1회 후 재개. 중복 0, 누락 0."""
    spec = _spec(tmp_path)

    def crash_after_page_one(done_count: int) -> None:
        if done_count >= 1:
            raise _StopAfterNPages("stop after page 1 (simulated crash)")

    with pytest.raises(_StopAfterNPages):
        run_pipeline(spec, on_unit_complete=crash_after_page_one)

    def stop_after_two_more_pages(done_count: int) -> None:
        if done_count >= 2:
            raise _StopAfterNPages("stop after 3 total pages")

    with pytest.raises(_StopAfterNPages):
        run_pipeline(spec, on_unit_complete=stop_after_two_more_pages)  # 재개 — 커서에서 이어서

    files = sorted((tmp_path / "out").glob("*.parquet"))
    assert len(files) == 3  # 1(크래시 전) + 2(재개 후) = 3페이지, 중복 파일 없음

    numbers: list[int] = []
    for f in files:
        table = pq.read_table(f)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        numbers.extend(row["number"] for row in table.to_pylist())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportUnknownVariableType]
    assert len(numbers) == len(set(numbers))  # 중복 0 — 같은 issue가 두 번 안 실린다
