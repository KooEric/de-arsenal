import sqlite3
import textwrap
from pathlib import Path
from typing import Literal

import httpx
import pyarrow.parquet as pq
import pytest
import respx

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import (
    DatabaseSourceSpec,
    FileSourceSpec,
    PaginationSpec,
    ParquetSinkSpec,
    PipelineSpec,
    PythonSourceSpec,
    RestSourceSpec,
    SinkSpec,
    SplitSpec,
    ValidateRule,
    ValidateSpec,
)
from arsenal_core.state import StateStore, UnitSpec
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
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
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


@respx.mock
def test_cursor_crash_resume(tmp_path: Path) -> None:
    """B6: 크래시 후 재실행 시 첫 요청이 영속된 커서를 실어 보낸다 — 처음부터 재시작하지
    않는다. 두 실행에 걸쳐 중복 행도 없다.
    """
    chain: dict[str | None, tuple[list[dict[str, int]], str | None]] = {
        None: ([{"id": 1}], "c1"),
        "c1": ([{"id": 2}], "c2"),
        "c2": ([{"id": 3}], None),
    }
    seen_afters: list[str | None] = []

    def responder(request: httpx.Request) -> httpx.Response:
        after = dict(request.url.params).get("after")
        seen_afters.append(after)
        rows, next_cursor = chain[after]
        return httpx.Response(200, json={"data": rows, "meta": {"next": next_cursor}})

    respx.get("https://api.test/cursor-items").mock(side_effect=responder)

    spec = PipelineSpec(
        name="cursor-t",
        state_dir=tmp_path / ".arsenal",
        source=RestSourceSpec(
            type="rest",
            url="https://api.test/cursor-items",
            pagination=PaginationSpec(
                mode="cursor",
                size_param="limit",
                size=1,
                cursor_param="after",
                cursor_path="meta.next",
                record_path="data",
            ),
        ),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )

    def crash_after_first(done_count: int) -> None:
        if done_count >= 1:
            raise SimulatedCrash

    with pytest.raises(SimulatedCrash):
        run_pipeline(spec, on_unit_complete=crash_after_first)
    assert seen_afters == [None]  # 크래시 전 딱 한 번만 요청됨

    seen_afters.clear()
    run_pipeline(spec)  # 같은 명령 그대로 재실행 (처음부터가 아니라 커서에서 재개)
    assert seen_afters[0] == "c1"  # 재개 시 첫 요청이 영속된 커서를 실어 보낸다

    files = sorted((tmp_path / "out").glob("*.parquet"))
    rows: list[dict[str, int]] = []
    for f in files:
        table = pq.read_table(f)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        rows.extend(table.to_pylist())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    all_ids = sorted(row["id"] for row in rows)
    assert all_ids == [1, 2, 3]  # 중복 0, 누락 0


@respx.mock
def test_cursor_rerun_after_completion_is_noop(tmp_path: Path) -> None:
    """M2-B (CRITICAL): 커서 트래버설이 next_cursor=None까지 완주한 뒤 재실행하면,
    이전엔 마지막 실제 커서가 store에 남아 있어 재실행 시 그 unit이 is_done으로
    skip되면서 fetch()가 절대 안 불려 _cursor_exhausted도 안 갱신되고 같은 unit이
    영원히 재생산되는 무한루프였다 (크래시가 마지막 mark_done 직후에 나도 동일).

    수정 후: 완료 시 SOURCE_EXHAUSTED 센티널을 커서로 영속 → 재실행 시 RestSource가
    그 센티널로 seed되고 _cursor_units()가 unit을 하나도 안 내 즉시 종료(clean no-op).
    HTTP 요청도 전혀 추가되지 않는다.
    """
    chain: dict[str | None, tuple[list[dict[str, int]], str | None]] = {
        None: ([{"id": 1}], "c1"),
        "c1": ([{"id": 2}], "c2"),
        "c2": ([{"id": 3}], None),
    }
    call_count = 0

    def responder(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        after = dict(request.url.params).get("after")
        rows, next_cursor = chain[after]
        return httpx.Response(200, json={"data": rows, "meta": {"next": next_cursor}})

    respx.get("https://api.test/cursor-rerun-items").mock(side_effect=responder)

    spec = PipelineSpec(
        name="cursor-rerun-t",
        state_dir=tmp_path / ".arsenal",
        source=RestSourceSpec(
            type="rest",
            url="https://api.test/cursor-rerun-items",
            pagination=PaginationSpec(
                mode="cursor",
                size_param="limit",
                size=1,
                cursor_param="after",
                cursor_path="meta.next",
                record_path="data",
            ),
        ),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )

    report1 = run_pipeline(spec)  # 첫 실행: 3페이지 다 수집, next_cursor=None으로 완주
    assert report1.written == 3
    calls_after_first_run = call_count

    # 재실행 — 수정 전엔 여기서 무한루프. 반드시 즉시 반환해야 한다.
    report2 = run_pipeline(spec)
    assert report2.fetched == 0
    assert report2.written == 0
    assert call_count == calls_after_first_run  # 새 HTTP 요청 0건

    files = sorted((tmp_path / "out").glob("*.parquet"))
    assert len(files) == 3  # 재실행이 중복 파일을 만들지 않음


def test_run_pipeline_dispatches_file_source(tmp_path: Path) -> None:
    """runner._build_source가 file 타입을 FileSource로 올바르게 배선하는지 종단 검증."""
    (tmp_path / "a.csv").write_text("id,v\n1,x\n2,y\n")
    spec = PipelineSpec(
        name="file-t",
        state_dir=tmp_path / ".arsenal",
        source=FileSourceSpec(type="file", path=str(tmp_path / "*.csv")),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
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
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )
    report = run_pipeline(spec)
    assert report.fetched == 1
    assert report.written == 1
    files = list((tmp_path / "out").glob("*.parquet"))
    assert len(files) == 1
    table = pq.read_table(files[0])  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    ids = sorted(row["id"] for row in table.to_pylist())  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]
    assert ids == [1, 2, 3, 4, 5]


@respx.mock
def test_run_records_schema_snapshot(tmp_path: Path) -> None:
    """M2-G: run 중 첫 non-empty batch의 스키마가 StateStore에 기록된다 (기록만 —
    탐지/정책은 P1)."""
    mock_pages([[{"id": 1}, {"id": 2}], [{"id": 3}]])
    spec = make_spec(tmp_path)
    run_pipeline(spec)

    store = StateStore(spec.state_dir / f"{spec.name}.db")
    try:
        schema_json = store.last_schema(spec.name)
    finally:
        store.close()
    assert schema_json is not None
    assert "id" in schema_json


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
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )
    report = run_pipeline(spec)
    assert report.fetched == 1
    assert report.written == 1
    files = list((tmp_path / "out").glob("*.parquet"))
    assert len(files) == 1
    table = pq.read_table(files[0])  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    ids = sorted(row["id"] for row in table.to_pylist())  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]
    assert ids == [1, 2]


@respx.mock
def test_run_pipeline_closes_rest_source_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`arsenal run`은 한 프로세스에서 여러 파이프라인을 돈다 — 매 run_pipeline이
    RestSource용 httpx.Client를 만들므로, finally에서 닫지 않으면 커넥션 풀이 샌다.
    """
    mock_pages([[{"id": 1}]])
    created: list[httpx.Client] = []
    real_client_cls = httpx.Client

    class TrackingClient(real_client_cls):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr("pugio.runner.httpx.Client", TrackingClient)
    run_pipeline(make_spec(tmp_path))
    assert len(created) == 1
    assert created[0].is_closed is True


def test_fetch_failure_propagates_and_marks_unit_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """source.fetch가 FatalError를 던지면 예외가 전파되고 StateStore에 failed로 기록된다
    (runner.py의 except → mark_failed → raise 경로, 이전까지 커버리지 없었음).
    """
    (tmp_path / "failing_source.py").write_text(
        textwrap.dedent(
            """
            from arsenal_core.errors import FatalError
            from arsenal_core.state import UnitSpec

            class FailingSource:
                def __init__(self, options, *, pipeline):
                    self._pipeline = pipeline

                def units(self):
                    yield UnitSpec.create(
                        pipeline=self._pipeline, source="mem", unit_key="only", payload={}
                    )

                def fetch(self, unit):
                    raise FatalError("boom")
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))  # pyright: ignore[reportUnknownMemberType]
    spec = PipelineSpec(
        name="fail-t",
        state_dir=tmp_path / ".arsenal",
        source=PythonSourceSpec(type="python", target="failing_source:FailingSource"),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )

    with pytest.raises(FatalError, match="boom"):
        run_pipeline(spec)

    expected_unit_id = UnitSpec.create(
        pipeline="fail-t", source="mem", unit_key="only", payload={}
    ).unit_id
    store = StateStore(spec.state_dir / f"{spec.name}.db")
    try:
        rec = store.get(expected_unit_id)
        assert rec.status == "failed"
        assert rec.attempts == 1
        assert rec.last_error is not None and "boom" in rec.last_error
    finally:
        store.close()


def test_sink_write_failure_marks_unit_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sink.write가 예외를 던지면 mark_failed로 기록되고 예외가 전파된다 (M2-F FIX 7,
    이전까지 sink.write는 runner의 try/except로 감싸지지 않아 unit이 'running'에
    영원히 갇혔다)."""
    (tmp_path / "a.csv").write_text("id,v\n1,x\n")
    spec = PipelineSpec(
        name="sink-fail-t",
        state_dir=tmp_path / ".arsenal",
        source=FileSourceSpec(type="file", path=str(tmp_path / "*.csv")),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )

    class FailingSink:
        def write(self, unit: UnitSpec, batch: object) -> None:
            raise FatalError("sink boom")

    def fake_build_sink(sink_spec: SinkSpec) -> FailingSink:
        return FailingSink()

    monkeypatch.setattr("pugio.runner.build_sink", fake_build_sink)

    with pytest.raises(FatalError, match="sink boom"):
        run_pipeline(spec)

    assert spec.source.type == "file"
    uid = UnitSpec.create(
        pipeline=spec.name, source=spec.source.path, unit_key="a.csv", payload={}
    ).unit_id
    store = StateStore(spec.state_dir / f"{spec.name}.db")
    try:
        assert store.status(uid) == "failed"
    finally:
        store.close()


def _make_validate_file_spec(
    tmp_path: Path, *, on_violation: Literal["block", "quarantine", "warn"]
) -> PipelineSpec:
    """3개 파일 unit — 정렬 순서상 두 번째(b.csv)가 not_null 위반을 낸다."""
    (tmp_path / "a.csv").write_text("id,v\n1,x\n")
    (tmp_path / "b.csv").write_text("id,v\n2,\n")  # v가 빈 문자열 → not_null 위반
    (tmp_path / "c.csv").write_text("id,v\n3,z\n")
    return PipelineSpec(
        name="validate-t",
        state_dir=tmp_path / ".arsenal",
        source=FileSourceSpec(type="file", path=str(tmp_path / "*.csv")),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
        validate=ValidateSpec(
            rules=[ValidateRule(field="v", not_null=True)], on_violation=on_violation
        ),
    )


def test_quarantine_isolates_unit_and_run_continues(tmp_path: Path) -> None:
    spec = _make_validate_file_spec(tmp_path, on_violation="quarantine")
    report = run_pipeline(spec)

    assert report.fetched == 3
    assert report.written == 2
    assert report.quarantined == 1

    store = StateStore(spec.state_dir / f"{spec.name}.db")
    try:
        counts = store.counts(spec.name)
    finally:
        store.close()
    assert counts.get("quarantined") == 1
    assert counts.get("done") == 2

    dlq_files = list((tmp_path / ".arsenal" / "dlq" / spec.name).glob("*.parquet"))
    assert len(dlq_files) == 1


def test_block_policy_raises_fatal(tmp_path: Path) -> None:
    """block 정책의 FatalError는 _fetch_with_auth_refresh를 감싼 except 바깥에서
    던져지므로, 명시적으로 mark_failed하지 않으면 unit이 영원히 'running'에 갇힌다
    (M2-E FIX 3). raise 이후 status가 'failed'여야 한다 — 'running'이면 회귀."""
    spec = _make_validate_file_spec(tmp_path, on_violation="block")
    with pytest.raises(FatalError):
        run_pipeline(spec)

    assert spec.source.type == "file"
    uid = UnitSpec.create(
        pipeline=spec.name, source=spec.source.path, unit_key="b.csv", payload={}
    ).unit_id
    store = StateStore(spec.state_dir / f"{spec.name}.db")
    try:
        assert store.status(uid) == "failed"
    finally:
        store.close()


def test_warn_policy_writes_anyway(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    spec = _make_validate_file_spec(tmp_path, on_violation="warn")
    with caplog.at_level("WARNING", logger="pugio.runner"):
        report = run_pipeline(spec)

    assert report.fetched == 3
    assert report.written == 3
    assert report.quarantined == 0

    store = StateStore(spec.state_dir / f"{spec.name}.db")
    try:
        counts = store.counts(spec.name)
    finally:
        store.close()
    assert counts.get("done") == 3

    # M2-E FIX 6: warn 정책은 print(stderr) 대신 logging을 쓴다.
    assert any("validation violations" in rec.message for rec in caplog.records)
