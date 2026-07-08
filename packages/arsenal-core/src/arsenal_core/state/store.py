"""SQLite(WAL) 영속 상태 저장소. 상태는 절대 메모리에만 두지 않는다.

핵심 불변식: "sink 쓰기 성공 → done 마킹" 순서 (docs/02-architecture.md).
단위 지표(row_count/byte_count/duration_ms)는 비용 가시성의 원료 — P0부터 기록 (docs/07).
구현: docs/plans/2026-07-08-m1-core-foundation.md Task 3
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
    unit_id    TEXT PRIMARY KEY,
    pipeline   TEXT NOT NULL,
    unit_key   TEXT NOT NULL,
    payload    TEXT NOT NULL,
    status     TEXT NOT NULL,          -- pending | running | done | failed | quarantined
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    row_count   INTEGER,
    byte_count  INTEGER,
    duration_ms INTEGER,
    created_at TEXT NOT NULL,          -- ISO8601 UTC
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_units_pipeline_status ON units(pipeline, status);
CREATE TABLE IF NOT EXISTS cursors (
    pipeline TEXT NOT NULL, source TEXT NOT NULL,
    cursor TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (pipeline, source)
);
CREATE TABLE IF NOT EXISTS schema_snapshots (
    pipeline    TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    schema_json TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class UnitSpec:
    """수집·처리의 최소 단위. 결정적 ID를 가진다."""

    unit_id: str
    pipeline: str
    unit_key: str  # 사람이 읽는 키 (예: "offset=200")
    payload: dict[str, Any]  # 요청 재구성에 필요한 파라미터

    @classmethod
    def create(
        cls, *, pipeline: str, source: str, unit_key: str, payload: dict[str, Any]
    ) -> "UnitSpec":
        """identity.unit_id로 결정적 ID를 만들어 생성한다."""
        raise NotImplementedError("M1 Task 3 — docs/plans/2026-07-08-m1-core-foundation.md")


@dataclass(frozen=True)
class UnitRecord:
    unit_id: str
    status: str
    attempts: int
    last_error: str | None


@dataclass(frozen=True)
class UnitMetrics:
    row_count: int | None
    byte_count: int | None
    duration_ms: int | None


class StateStore:
    """unit 상태·커서·스키마 스냅샷의 영속 저장소.

    상태 전이: pending → running → done | failed | quarantined
    (Retryable이면 running → pending 복귀, attempts < max 한정)
    """

    def __init__(self, db_path: Path) -> None:
        raise NotImplementedError("M1 Task 3")

    def register(self, unit: UnitSpec) -> None:
        """INSERT OR IGNORE — 두 번 등록해도 안전 (멱등)."""
        raise NotImplementedError("M1 Task 3")

    def mark_running(self, uid: str) -> None:
        raise NotImplementedError("M1 Task 3")

    def mark_done(
        self,
        uid: str,
        *,
        row_count: int | None = None,
        byte_count: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        raise NotImplementedError("M1 Task 3")

    def mark_failed(self, uid: str, error: str) -> None:
        """attempts를 1 올리고 last_error 기록."""
        raise NotImplementedError("M1 Task 3")

    def status(self, uid: str) -> str | None:
        raise NotImplementedError("M1 Task 3")

    def is_done(self, uid: str) -> bool:
        raise NotImplementedError("M1 Task 3")

    def get(self, uid: str) -> UnitRecord:
        raise NotImplementedError("M1 Task 3")

    def metrics(self, uid: str) -> UnitMetrics:
        raise NotImplementedError("M1 Task 3")

    def counts(self, pipeline: str) -> dict[str, int]:
        """status → 개수. `pugio status`의 데이터."""
        raise NotImplementedError("M1 Task 3")

    def close(self) -> None:
        raise NotImplementedError("M1 Task 3")
