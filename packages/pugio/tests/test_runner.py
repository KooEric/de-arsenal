import sqlite3
import textwrap
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest
import respx

from arsenal_core.spec.models import (
    DatabaseSourceSpec,
    FileSourceSpec,
    PaginationSpec,
    PipelineSpec,
    PythonSourceSpec,
    RestSourceSpec,
    SinkSpec,
    SplitSpec,
)
from pugio.runner import run_pipeline


def make_spec(tmp_path: Path) -> PipelineSpec:
    return PipelineSpec(
        name="t",
        state_dir=tmp_path / ".arsenal",
        source=RestSourceSpec(
            type="rest",
            url="https://api.test/items",
            pagination=PaginationSpec(mode="offset", size=2),
        ),
        sink=SinkSpec(type="parquet", path=tmp_path / "out"),
    )


def mock_pages(pages: list[list[dict[str, int]]]) -> None:
    """offset 파라미터에 따라 해당 페이지를 응답하는 목 API."""

    def responder(request: httpx.Request) -> httpx.Response:
        offset = int(dict(request.url.params)["offset"])
        idx = offset // 2
        body = pages[idx] if idx < len(pages) else []
        return httpx.Response(200, json=body)

    respx.get("https://api.test/items").mock(side_effect=responder)


@respx.mock
def test_happy_path_collects_all_pages(tmp_path: Path) -> None:
    mock_pages([[{"id": 1}, {"id": 2}], [{"id": 3}, {"id": 4}], [{"id": 5}]])
    report = run_pipeline(make_spec(tmp_path))
    assert report.fetched == 3
    assert report.written == 3
    assert len(list((tmp_path / "out").glob("*.parquet"))) == 3


@respx.mock
def test_rerun_after_completion_is_noop(tmp_path: Path) -> None:
    mock_pages([[{"id": 1}, {"id": 2}], [{"id": 3}]])
    spec = make_spec(tmp_path)
    run_pipeline(spec)
    report2 = run_pipeline(spec)  # 재실행은 정상 동작
    assert report2.written == 0
    assert report2.skipped >= 1  # 완료분은 건너뜀


class SimulatedCrash(Exception):
    pass


@respx.mock
def test_crash_and_resume_no_dup_no_loss(tmp_path: Path) -> None:
    """시나리오 B: 도중에 죽어도 재실행하면 이어서. 중복 0, 누락 0."""
    import pyarrow.parquet as pq

    mock_pages([[{"id": 1}, {"id": 2}], [{"id": 3}, {"id": 4}], [{"id": 5}]])
    spec = make_spec(tmp_path)

    def crash_after_first(done_count: int) -> None:
        if done_count >= 1:
            raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        run_pipeline(spec, on_unit_complete=crash_after_first)

    report = run_pipeline(spec)  # 같은 명령 그대로 재실행
    assert report.skipped == 1  # 완료했던 1개는 다시 받지 않음
    files = sorted((tmp_path / "out").glob("*.parquet"))
    # pyarrow.parquet has no type stubs; read_table()/to_pylist()'s types are Unknown.
    rows: list[dict[str, int]] = []
    for f in files:
        table = pq.read_table(f)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        rows.extend(table.to_pylist())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    all_ids = sorted(row["id"] for row in rows)
    assert all_ids == [1, 2, 3, 4, 5]  # 중복 0, 누락 0


def test_run_pipeline_dispatches_file_source(tmp_path: Path) -> None:
    """runner._build_source가 file 타입을 FileSource로 올바르게 배선하는지 종단 검증."""
    (tmp_path / "a.csv").write_text("id,v\n1,x\n2,y\n")
    spec = PipelineSpec(
        name="file-t",
        state_dir=tmp_path / ".arsenal",
        source=FileSourceSpec(type="file", path=str(tmp_path / "*.csv")),
        sink=SinkSpec(type="parquet", path=tmp_path / "out"),
    )
    report = run_pipeline(spec)
    assert report.fetched == 1
    assert report.written == 1
    files = list((tmp_path / "out").glob("*.parquet"))
    assert len(files) == 1
    table = pq.read_table(files[0])  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    ids = sorted(row["id"] for row in table.to_pylist())  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]
    assert ids == [1, 2]


def test_run_pipeline_dispatches_database_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """runner._build_source가 database 타입을 DatabaseSource로 올바르게 배선하는지 종단 검증."""
    db = tmp_path / "src.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO orders VALUES (?, ?)", [(i, "x") for i in range(1, 6)])
    con.commit()
    con.close()
    monkeypatch.setenv("RUNNER_SRC_DB", str(db))
    spec = PipelineSpec(
        name="db-t",
        state_dir=tmp_path / ".arsenal",
        source=DatabaseSourceSpec(
            type="database",
            dialect="sqlite",
            dsn_env="RUNNER_SRC_DB",
            table="orders",
            split=SplitSpec(key="id", chunk=10),
        ),
        sink=SinkSpec(type="parquet", path=tmp_path / "out"),
    )
    report = run_pipeline(spec)
    assert report.fetched == 1
    assert report.written == 1
    files = list((tmp_path / "out").glob("*.parquet"))
    assert len(files) == 1
    table = pq.read_table(files[0])  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    ids = sorted(row["id"] for row in table.to_pylist())  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]
    assert ids == [1, 2, 3, 4, 5]


def test_run_pipeline_dispatches_python_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """runner._build_source가 python 타입을 load_python_source로 올바르게 배선하는지 종단 검증."""
    (tmp_path / "runner_test_source.py").write_text(
        textwrap.dedent(
            """
            import pyarrow as pa
            from arsenal_core.state import UnitSpec
            from pugio.sources.base import FetchResult

            class RunnerTestSource:
                def __init__(self, options, *, pipeline):
                    self._pipeline = pipeline

                def units(self):
                    yield UnitSpec.create(
                        pipeline=self._pipeline, source="mem", unit_key="only", payload={}
                    )

                def fetch(self, unit):
                    return FetchResult(
                        batch=pa.RecordBatch.from_pylist([{"id": 1}, {"id": 2}]),
                        exhausted=True,
                    )
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))  # pyright: ignore[reportUnknownMemberType]
    spec = PipelineSpec(
        name="py-t",
        state_dir=tmp_path / ".arsenal",
        source=PythonSourceSpec(type="python", target="runner_test_source:RunnerTestSource"),
        sink=SinkSpec(type="parquet", path=tmp_path / "out"),
    )
    report = run_pipeline(spec)
    assert report.fetched == 1
    assert report.written == 1
    files = list((tmp_path / "out").glob("*.parquet"))
    assert len(files) == 1
    table = pq.read_table(files[0])  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    ids = sorted(row["id"] for row in table.to_pylist())  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]
    assert ids == [1, 2]
