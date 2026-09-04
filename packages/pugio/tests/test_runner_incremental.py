"""증분 수집을 러너 전체로 통과시켜 검증한다 — 소스 단위 테스트가 못 잡는 것들.

여기서 고정하는 것: 워터마크가 상태 DB에 남는가, 재실행이 완료된 창을 다시 받지
않는가, 창 도중에 죽으면 그 창의 남은 페이지부터 이어받는가, 그리고 증분 +
quarantine의 조용한 누락 위험을 경고하는가.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pyarrow.parquet as pq
import pytest
import respx
from _pytest.logging import LogCaptureFixture
from typer.testing import CliRunner

from arsenal_core.spec.models import (
    PaginationSpec,
    ParquetSinkSpec,
    PipelineSpec,
    RestIncrementalSpec,
    RestSourceSpec,
    ValidateRule,
    ValidateSpec,
)
from arsenal_core.state import StateStore
from pugio.cli import app
from pugio.runner import run_pipeline

URL = "https://api.test/orders"
runner = CliRunner()


def make_spec(tmp_path: Path, *, days_ago: int = 3, size: int = 2) -> PipelineSpec:
    """지금으로부터 `days_ago`일 전부터 1일 창 — 정확히 `days_ago`개의 완결된 창.

    부분 창은 열거되지 않으므로(iter_windows) 실행 시각이 조금 흘러도 개수가 같다.
    """
    start = (datetime.now(UTC) - timedelta(days=days_ago)).replace(microsecond=0)
    return PipelineSpec(
        name="inc",
        state_dir=str(tmp_path / ".arsenal"),
        source=RestSourceSpec(
            type="rest",
            url=URL,
            pagination=PaginationSpec(mode="offset", size=size),
            incremental=RestIncrementalSpec(
                since_param="updated_after",
                until_param="updated_before",
                start=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                window="1d",
            ),
        ),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )


def mock_one_page_per_window() -> None:
    """창마다 그 창의 시작 시각을 담은 한 행 — 어떤 창을 받았는지 결과에서 읽는다."""

    def responder(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if int(params["offset"]) > 0:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[{"w": params["updated_after"]}])

    respx.get(URL).mock(side_effect=responder)


def mock_two_pages_per_window() -> None:
    """창마다 꽉 찬 페이지 1개 + 짧은 페이지 1개 — 창 도중 중단을 만들 수 있다."""

    def responder(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        since = params["updated_after"]
        offset = int(params["offset"])
        if offset == 0:
            return httpx.Response(200, json=[{"w": since, "n": 1}, {"w": since, "n": 2}])
        if offset == 2:
            return httpx.Response(200, json=[{"w": since, "n": 3}])
        return httpx.Response(200, json=[])

    respx.get(URL).mock(side_effect=responder)


def collected_windows(tmp_path: Path) -> list[str]:
    # pyarrow.parquet에는 타입 스텁이 없다 — 레포 관례대로 이 지점만 무시한다.
    table = pq.read_table(tmp_path / "out")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    values = cast("list[str]", table.column("w").to_pylist())  # pyright: ignore[reportUnknownMemberType]
    return sorted(set(values))


def watermark(spec: PipelineSpec) -> str | None:
    store = StateStore(Path(spec.state_dir) / f"{spec.name}.db")
    try:
        return store.get_cursor(spec.name, URL)
    finally:
        store.close()


@respx.mock
def test_collects_one_unit_per_completed_window(tmp_path: Path) -> None:
    mock_one_page_per_window()
    spec = make_spec(tmp_path)

    report = run_pipeline(spec)

    assert report.fetched == 3
    assert report.written == 3
    assert len(collected_windows(tmp_path)) == 3


@respx.mock
def test_watermark_persists_at_the_end_of_the_last_window(tmp_path: Path) -> None:
    mock_one_page_per_window()
    spec = make_spec(tmp_path)
    run_pipeline(spec)

    saved = watermark(spec)
    assert saved is not None
    # 마지막 창의 끝은 시작 + 3일이고, 그 시점은 아직 미래가 아니다.
    assert datetime.strptime(saved, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) <= datetime.now(UTC)


@respx.mock
def test_rerun_is_a_clean_no_op_until_the_next_window_completes(tmp_path: Path) -> None:
    """완료된 창은 다시 열거되지 않는다 — skip조차 없이 unit이 0개다."""
    mock_one_page_per_window()
    spec = make_spec(tmp_path)
    run_pipeline(spec)
    before = watermark(spec)

    second = run_pipeline(spec)

    assert (second.fetched, second.written, second.skipped) == (0, 0, 0)
    assert watermark(spec) == before
    assert len(collected_windows(tmp_path)) == 3


@respx.mock
def test_interrupted_window_resumes_from_its_remaining_page(tmp_path: Path) -> None:
    """창 중간에서 죽으면 워터마크가 전진하지 않아 그 창을 다시 열거하고, 이미 받은
    페이지는 skip한다 — 재개 비용이 "진행 중이던 창 하나"로 유지되는 지점."""
    mock_two_pages_per_window()
    spec = make_spec(tmp_path, days_ago=2)

    def crash_after_first_page(done: int) -> None:
        if done == 1:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_pipeline(spec, on_unit_complete=crash_after_first_page)

    # 첫 창의 1페이지만 done — 창을 완주하지 않았으므로 워터마크는 아직 없다.
    assert watermark(spec) is None

    report = run_pipeline(spec)

    assert report.skipped == 1  # 이미 받은 첫 페이지는 다시 받지 않는다
    assert watermark(spec) is not None
    assert len(collected_windows(tmp_path)) == 2  # 두 창 모두 수집됐다


@respx.mock
def test_quarantine_policy_warns_about_silent_gaps(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    """격리는 워터마크를 붙잡지 못한다 — 증분에서는 block이 옳다는 것을 알린다."""
    mock_one_page_per_window()
    spec = make_spec(tmp_path).model_copy(
        update={
            "validation": ValidateSpec(
                rules=[ValidateRule(field="w", not_null=True)], on_violation="quarantine"
            )
        }
    )

    with caplog.at_level("WARNING", logger="pugio.runner"):
        run_pipeline(spec)

    assert any("quarantine" in record.message for record in caplog.records)


@respx.mock
def test_block_policy_does_not_warn(tmp_path: Path, caplog: LogCaptureFixture) -> None:
    mock_one_page_per_window()
    spec = make_spec(tmp_path).model_copy(
        update={
            "validation": ValidateSpec(
                rules=[ValidateRule(field="w", not_null=True)], on_violation="block"
            )
        }
    )

    with caplog.at_level("WARNING", logger="pugio.runner"):
        run_pipeline(spec)

    assert not [r for r in caplog.records if "quarantine" in r.message]


def test_dlq_retry_is_refused_for_incremental_pipelines(tmp_path: Path) -> None:
    """워터마크가 그 창을 지나갔으면 어떤 실행도 그 unit을 다시 등록하지 않는다 —
    requeue는 고아를 만들 뿐이라 cursor/link와 같은 이유로 거부한다."""
    spec_path = tmp_path / "collect.yaml"
    spec_path.write_text(
        "name: inc\n"
        f"state_dir: {tmp_path / '.arsenal'}\n"
        "source:\n"
        "  type: rest\n"
        f"  url: {URL}\n"
        "  pagination: {mode: offset, size: 2}\n"
        "  incremental:\n"
        "    since_param: updated_after\n"
        "    start: '2026-01-01T00:00:00Z'\n"
        "sink:\n"
        f"  type: parquet\n  path: {tmp_path / 'out'}\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["dlq", "retry", str(spec_path), "--unit", "abc"])

    assert result.exit_code == 1
    assert "not supported for incremental pipelines" in result.output
