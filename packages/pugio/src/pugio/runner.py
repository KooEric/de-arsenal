"""수집 루프. 사용자가 보는 것은 선언뿐 — 재시도·체크포인트·재개는 여기가 흡수한다.

흐름 (docs/02-architecture.md "수집 한 사이클"):
  units() 열거 → register → done이면 skip → fetch(with_retry) → sink.write → mark_done

핵심 불변식: "sink 쓰기 성공 → done 마킹" 순서.
at-least-once 실행 + 멱등 쓰기 = exactly-once 결과.
구현: docs/plans/2026-07-08-m1-core-foundation.md Task 8
"""

import functools
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import pyarrow as pa

from arsenal_core.errors import AuthExpiredError, FatalError
from arsenal_core.retry import DEFAULT_MAX_ATTEMPTS, with_retry
from arsenal_core.spec.models import PipelineSpec
from arsenal_core.state import SOURCE_EXHAUSTED, StateStore, UnitSpec
from pugio.auth import AuthProvider, build_auth
from pugio.dlq import write_dlq
from pugio.sinks import build_sink
from pugio.sources.base import FetchResult, Source
from pugio.sources.database import DatabaseSource
from pugio.sources.file import FileSource
from pugio.sources.python_source import load_python_source
from pugio.sources.rest import RestSource
from pugio.validate.gate import check

logger = logging.getLogger("pugio.runner")


@dataclass(frozen=True)
class RunReport:
    fetched: int
    written: int
    skipped: int  # done이라 건너뛴 unit 수 — 재개의 증거
    quarantined: int = 0  # 검증 위반으로 격리된 unit 수 (M2-E)


def _schema_json(batch: pa.RecordBatch) -> str:
    """batch 스키마를 결정적 JSON으로 직렬화 (필드 순서 = 스키마 필드 순서).

    schema_snapshots 기록용 (M2-G) — 탐지/정책은 P1, 여기는 기록만 한다.
    """
    return json.dumps(
        [{"name": f.name, "type": str(f.type), "nullable": f.nullable} for f in batch.schema]
    )


def _fetch_with_retry(source: Source, unit: UnitSpec, max_attempts: int) -> FetchResult:
    """with_retry(source.fetch(unit)) 래퍼 — 루프 안 클로저(B023 loop-variable 함정)를
    피하려고 top-level 함수로 뺐다."""
    return with_retry(functools.partial(source.fetch, unit), max_attempts=max_attempts)


def _build_source(spec: PipelineSpec, store: StateStore) -> tuple[Source, AuthProvider | None]:
    """spec.source.type으로 분기해 알맞은 Source 구현체를 만든다.

    discriminated union이라 spec.source.type이 좁혀지면 pyright도 필드를 좁혀 안다.
    rest cursor/link 모드는 StateStore에 영속된 커서로 재개한다 (M2-B).

    커서가 SOURCE_EXHAUSTED 센티널이면 이 트래버설은 이미 완료된 것 — RestSource에
    그대로 전달하면 _cursor_units()가 즉시 return해 unit을 하나도 내지 않는다.
    (재실행이 clean no-op이 되는 지점. 처음부터 다시 긁으려면 사용자가 state를
    지워야 한다 — DatabaseSource의 스냅샷 재개 시맨틱과 동일, P1 스코프 밖.)

    반환값의 AuthProvider는 러너의 refresh 루프가 명시적으로 쓴다 — RestSource의
    private _auth를 getattr로 훔쳐보는 대신, 이 함수가 만든 시점의 값을 그대로
    돌려준다 (rest가 아니면 None). Any-typed reflection을 없애 pyright가
    auth.refresh() 호출을 실제로 타입체크하게 한다.
    """
    src = spec.source
    if src.type == "rest":
        initial_cursor = None
        if src.pagination.mode in ("cursor", "link"):
            initial_cursor = store.get_cursor(spec.name, src.url)
        client = httpx.Client()
        # M2-D: auth가 None이면 build_auth도 None을 돌려준다 — RestSource는 그대로
        # spec.headers만 사용해 기존 호출부(무인증 스펙)를 깨지 않는다.
        auth = build_auth(src.auth, client)
        source = RestSource(
            src,
            pipeline=spec.name,
            client=client,
            initial_cursor=initial_cursor,
            auth=auth,
        )
        return source, auth
    if src.type == "file":
        return FileSource(src, pipeline=spec.name), None
    if src.type == "database":
        return DatabaseSource(src, pipeline=spec.name), None
    if src.type == "python":
        return load_python_source(src, pipeline=spec.name), None
    raise FatalError(f"unknown source type: {src.type}")  # pragma: no cover


def _fetch_with_auth_refresh(
    source: Source,
    unit: UnitSpec,
    auth: AuthProvider | None,
    max_attempts: int,
) -> FetchResult:
    """with_retry는 AuthExpiredError를 blind하게 재시도하지 않고 즉시 전파한다
    (retry.py M2-D) — 여기서 정확히 한 번 refresh() 후 같은 unit을 한 번 더
    시도한다. 그마저 401이면 AuthExpiredError가 다시 escape해 호출부(run_pipeline)의
    바깥 except가 failed로 기록한다."""
    try:
        return _fetch_with_retry(source, unit, max_attempts)
    except AuthExpiredError:
        if auth is None:
            raise
        auth.refresh()
        return _fetch_with_retry(source, unit, max_attempts)


def run_pipeline(
    spec: PipelineSpec,
    *,
    on_unit_complete: Callable[[int], None] | None = None,  # 테스트 훅 (크래시 주입)
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> RunReport:
    store = StateStore(spec.state_dir / f"{spec.name}.db")
    source, auth = _build_source(spec, store)
    # SinkSpec은 discriminated union(parquet/duckdb/postgres, M2-A) — build_sink가
    # spec.type으로 알맞은 구현체를 만든다 (M2-F).
    sink = build_sink(spec.sink)
    fetched = written = skipped = done_count = quarantined = 0
    schema_recorded = False  # 이번 run에서 스냅샷 기록 여부 (첫 non-empty batch에서 한 번만)

    try:
        for unit in source.units():
            store.register(unit)
            if store.is_done(unit.unit_id):
                skipped += 1
                continue
            store.mark_running(unit.unit_id)
            started = time.monotonic()
            try:
                result = _fetch_with_auth_refresh(source, unit, auth, max_attempts)
            except Exception as e:
                store.mark_failed(unit.unit_id, str(e))
                raise
            fetched += 1
            if not schema_recorded and result.batch is not None:
                store.snapshot_schema(spec.name, _schema_json(result.batch))
                schema_recorded = True
            if spec.validation is not None and result.batch is not None:
                report = check(result.batch, spec.validation.rules)
                if not report.ok:
                    policy = spec.validation.on_violation
                    if policy == "block":
                        # M2-E FIX 3: 이 FatalError는 _fetch_with_auth_refresh를 감싼
                        # try/except(Exception → mark_failed → raise) 바깥에서 던져지므로,
                        # 명시적으로 failed를 기록하지 않으면 unit이 영원히 'running'에
                        # 갇힌다 (mark_running은 위에서 이미 호출됨). quarantine/warn은
                        # 그대로 done/quarantined로 전이하므로 영향 없다.
                        msg = f"validation failed for unit {unit.unit_key}: {report.violations}"
                        store.mark_failed(unit.unit_id, msg)
                        raise FatalError(msg)
                    if policy == "warn":
                        logger.warning(
                            "validation violations in unit %s: %s",
                            unit.unit_key,
                            report.violations,
                        )
                    if policy == "quarantine":
                        write_dlq(spec.state_dir, spec.name, unit, result.batch, report.violations)
                        reason = "; ".join(
                            f"{v.rule}:{v.field}={v.count}" for v in report.violations
                        )
                        store.mark_quarantined(unit.unit_id, reason)
                        quarantined += 1
                        # 쓰기·done 마킹 모두 건너뛰고 다음 unit으로 — 격리된 unit은
                        # 커서를 진전시키지 않는다 (재실행 시 여전히 격리 상태로 재등록됨).
                        #
                        # M4 FIX (api-to-postgres 레시피 E2E에서 발견): offset/page
                        # 페이지네이션은 무한 generator(itertools.count)라 이 루프를 끝내는
                        # 유일한 신호가 아래쪽의 `if result.exhausted: break`뿐이다. 이
                        # continue가 그 지점을 건너뛰므로, 검증 실패가 하필 마지막
                        # 페이지(짧은 행 수로 exhausted를 알리는 바로 그 페이지)에서 나면
                        # 러너가 종료 신호를 영영 못 보고 존재하지 않는 다음 페이지를
                        # 무한히 요청한다. cursor/link 모드는 소스 내부 상태로 스스로
                        # 멈추므로 이 break는 사실상 no-op이지만, offset/page 모드에는
                        # 필수다.
                        if result.exhausted:
                            break
                        continue
            if result.batch is not None:
                # M2-F FIX 7: sink.write는 fetch와 달리 감싸지지 않아, 실패해도
                # mark_failed 없이 unit이 'running'에 영원히 갇혔다 (Retryable/Fatal
                # 분류도 무의미해짐). fetch와 동일하게 with_retry로 감싸 RetryableError는
                # 재시도하고, 그래도 실패하면(또는 FatalError면) mark_failed 후 전파한다.
                try:
                    with_retry(
                        functools.partial(sink.write, unit, result.batch),
                        max_attempts=max_attempts,
                    )
                except Exception as e:
                    store.mark_failed(unit.unit_id, str(e))
                    raise
                written += 1
            row_count = result.batch.num_rows if result.batch is not None else 0
            byte_count = result.batch.nbytes if result.batch is not None else 0
            duration_ms = int((time.monotonic() - started) * 1000)
            # done 마킹 + 커서 전진 (핵심 불변식의 확장): cursor/link 모드는 이 둘을
            # StateStore.mark_done_and_advance_cursor로 원자화해 한 커밋에 묶는다
            # (M2 최종 리뷰 FIX 1). 예전엔 mark_done()과 set_cursor()가 별개
            # 트랜잭션이라, 그 사이 크래시가 나면 unit은 done인데 커서는 그 unit을
            # 만든 이전 값에 멈춰 재실행 시 같은(이미 done인) unit을 다시 seed →
            # is_done skip → fetch() 미호출 → 커서 영원히 미전진(무한루프)이었다
            # (cursor/link의 모든 중간 페이지에 영향 — 마지막 페이지의
            # SOURCE_EXHAUSTED 센티널만 다뤘던 이전 수정으로는 부족했다).
            #
            # M2-B: cursor/link 모드가 next_cursor=None(=트래버설 완료)에 도달하면
            # SOURCE_EXHAUSTED 센티널을 커서로 영속해 재실행이 clean no-op이 되게
            # 한다 (완전 재수집은 P1, state를 지우면 됨).
            if spec.source.type == "rest" and spec.source.pagination.mode in ("cursor", "link"):
                cursor_value = (
                    result.next_cursor if result.next_cursor is not None else SOURCE_EXHAUSTED
                )
                store.mark_done_and_advance_cursor(
                    unit.unit_id,
                    pipeline=spec.name,
                    source=spec.source.url,
                    cursor=cursor_value,
                    row_count=row_count,
                    byte_count=byte_count,
                    duration_ms=duration_ms,
                )
            else:
                store.mark_done(
                    unit.unit_id,
                    row_count=row_count,
                    byte_count=byte_count,
                    duration_ms=duration_ms,
                )
            done_count += 1
            if on_unit_complete is not None:
                on_unit_complete(done_count)
            if result.exhausted:
                break
        return RunReport(fetched=fetched, written=written, skipped=skipped, quarantined=quarantined)
    finally:
        # close()는 Source 프로토콜의 선택적 훅 — httpx.Client 등 커넥션을 든 소스만
        # 구현한다 (RestSource). file/database/python 소스는 없으므로 getattr로 안전하게
        # 건너뛴다. 한 프로세스에서 여러 파이프라인을 도는 `arsenal run`에서 누수를 막는다.
        close = getattr(source, "close", None)
        if callable(close):
            close()
        store.close()
