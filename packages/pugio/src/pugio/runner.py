"""수집 루프. 사용자가 보는 것은 선언뿐 — 재시도·체크포인트·재개는 여기가 흡수한다.

흐름 (docs/02-architecture.md "수집 한 사이클"):
  units() 열거 → register → done이면 skip → fetch(with_retry) → sink.write → mark_done

핵심 불변식: "sink 쓰기 성공 → done 마킹" 순서.
at-least-once 실행 + 멱등 쓰기 = exactly-once 결과.
구현: docs/plans/2026-07-08-m1-core-foundation.md Task 8
"""

import functools
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from arsenal_core.errors import FatalError
from arsenal_core.retry import DEFAULT_MAX_ATTEMPTS, with_retry
from arsenal_core.spec.models import PipelineSpec
from arsenal_core.state import SOURCE_EXHAUSTED, StateStore
from pugio.sinks.parquet import ParquetSink
from pugio.sources.base import Source
from pugio.sources.database import DatabaseSource
from pugio.sources.file import FileSource
from pugio.sources.python_source import load_python_source
from pugio.sources.rest import RestSource


@dataclass(frozen=True)
class RunReport:
    fetched: int
    written: int
    skipped: int  # done이라 건너뛴 unit 수 — 재개의 증거


def _build_source(spec: PipelineSpec, store: StateStore) -> Source:
    """spec.source.type으로 분기해 알맞은 Source 구현체를 만든다.

    discriminated union이라 spec.source.type이 좁혀지면 pyright도 필드를 좁혀 안다.
    rest cursor/link 모드는 StateStore에 영속된 커서로 재개한다 (M2-B).

    커서가 SOURCE_EXHAUSTED 센티널이면 이 트래버설은 이미 완료된 것 — RestSource에
    그대로 전달하면 _cursor_units()가 즉시 return해 unit을 하나도 내지 않는다.
    (재실행이 clean no-op이 되는 지점. 처음부터 다시 긁으려면 사용자가 state를
    지워야 한다 — DatabaseSource의 스냅샷 재개 시맨틱과 동일, P1 스코프 밖.)
    """
    src = spec.source
    if src.type == "rest":
        initial_cursor = None
        if src.pagination.mode in ("cursor", "link"):
            initial_cursor = store.get_cursor(spec.name, src.url)
        return RestSource(
            src, pipeline=spec.name, client=httpx.Client(), initial_cursor=initial_cursor
        )
    if src.type == "file":
        return FileSource(src, pipeline=spec.name)
    if src.type == "database":
        return DatabaseSource(src, pipeline=spec.name)
    if src.type == "python":
        return load_python_source(src, pipeline=spec.name)
    raise FatalError(f"unknown source type: {src.type}")  # pragma: no cover


def run_pipeline(
    spec: PipelineSpec,
    *,
    on_unit_complete: Callable[[int], None] | None = None,  # 테스트 훅 (크래시 주입)
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> RunReport:
    store = StateStore(spec.state_dir / f"{spec.name}.db")
    source = _build_source(spec, store)
    # SinkSpec이 discriminated union이 되며 duckdb/postgres 멤버가 추가됐다 (M2-A).
    # 구현체는 아직 parquet뿐 — M2-F가 build_sink 팩토리로 이 분기를 대체한다.
    if spec.sink.type != "parquet":
        raise FatalError(f"only parquet sink implemented (duckdb/postgres: M2-F): {spec.sink.type}")
    # spec.sink.path는 str(URI 스킴 보존용) — 로컬 파일시스템 싱크는 여기서 Path로 감싼다.
    sink = ParquetSink(Path(spec.sink.path))
    fetched = written = skipped = done_count = 0

    try:
        for unit in source.units():
            store.register(unit)
            if store.is_done(unit.unit_id):
                skipped += 1
                continue
            store.mark_running(unit.unit_id)
            started = time.monotonic()
            try:
                result = with_retry(
                    functools.partial(source.fetch, unit), max_attempts=max_attempts
                )
            except Exception as e:
                store.mark_failed(unit.unit_id, str(e))
                raise
            fetched += 1
            if result.batch is not None:
                sink.write(unit, result.batch)  # 멱등 쓰기 먼저,
                written += 1
            store.mark_done(  # done 마킹은 그 다음 (핵심 불변식)
                unit.unit_id,
                row_count=result.batch.num_rows if result.batch is not None else 0,
                byte_count=result.batch.nbytes if result.batch is not None else 0,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            # 커서는 done 마킹 다음에 영속 — 쓰기→done→커서 순서로 불변식을 확장한다
            # (크래시가 done 마킹 전이면 커서도 안 넘어가 재개 시 같은 unit을 다시 받는다).
            #
            # M2-B: cursor/link 모드가 next_cursor=None(=트래버설 완료)에 도달했을 때
            # 예전엔 아무 것도 안 썼다 — 커서가 마지막 실제 페이지 값에 멈춰 있으니,
            # 재실행 시 이미 done인 그 unit을 다시 seed → is_done skip → continue를
            # 무한 반복 (fetch()가 다시 호출돼야 _cursor_exhausted가 갱신되는데, skip
            # 경로는 fetch()를 안 부른다). 완료를 SOURCE_EXHAUSTED 센티널로 명시적으로
            # 영속해 재실행이 clean no-op이 되게 한다 (완전 재수집은 P1, state를 지우면 됨).
            if spec.source.type == "rest" and spec.source.pagination.mode in ("cursor", "link"):
                if result.exhausted:
                    store.set_cursor(spec.name, spec.source.url, SOURCE_EXHAUSTED)
                elif result.next_cursor is not None:
                    store.set_cursor(spec.name, spec.source.url, result.next_cursor)
            done_count += 1
            if on_unit_complete is not None:
                on_unit_complete(done_count)
            if result.exhausted:
                break
        return RunReport(fetched=fetched, written=written, skipped=skipped)
    finally:
        # close()는 Source 프로토콜의 선택적 훅 — httpx.Client 등 커넥션을 든 소스만
        # 구현한다 (RestSource). file/database/python 소스는 없으므로 getattr로 안전하게
        # 건너뛴다. 한 프로세스에서 여러 파이프라인을 도는 `arsenal run`에서 누수를 막는다.
        close = getattr(source, "close", None)
        if callable(close):
            close()
        store.close()
