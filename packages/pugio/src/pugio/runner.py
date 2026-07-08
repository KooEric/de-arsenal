"""수집 루프. 사용자가 보는 것은 선언뿐 — 재시도·체크포인트·재개는 여기가 흡수한다.

흐름 (docs/02-architecture.md "수집 한 사이클"):
  units() 열거 → register → done이면 skip → fetch(with_retry) → sink.write → mark_done

핵심 불변식: "sink 쓰기 성공 → done 마킹" 순서.
at-least-once 실행 + 멱등 쓰기 = exactly-once 결과.
구현: docs/plans/2026-07-08-m1-core-foundation.md Task 8
"""

from collections.abc import Callable
from dataclasses import dataclass

from arsenal_core.retry import DEFAULT_MAX_ATTEMPTS
from arsenal_core.spec.models import PipelineSpec


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
    raise NotImplementedError("M1 Task 8 — docs/plans/2026-07-08-m1-core-foundation.md")
