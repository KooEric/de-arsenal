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

import httpx

from arsenal_core.retry import DEFAULT_MAX_ATTEMPTS, with_retry
from arsenal_core.spec.models import PipelineSpec
from arsenal_core.state import StateStore
from pugio.sinks.parquet import ParquetSink
from pugio.sources.rest import RestSource


@dataclass(frozen=True)
class RunReport:
    fetched: int
    written: int
    skipped: int  # done이라 건너뛴 unit 수 — 재개의 증거


def run_pipeline(
    spec: PipelineSpec,
    *,
    on_unit_complete: Callable[[int], None] | None = None,  # 테스트 훅 (크래시 주입)
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> RunReport:
    store = StateStore(spec.state_dir / f"{spec.name}.db")
    source = RestSource(spec.source, pipeline=spec.name, client=httpx.Client())
    sink = ParquetSink(spec.sink.path)
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
            done_count += 1
            if on_unit_complete is not None:
                on_unit_complete(done_count)
            if result.exhausted:
                break
        return RunReport(fetched=fetched, written=written, skipped=skipped)
    finally:
        store.close()
