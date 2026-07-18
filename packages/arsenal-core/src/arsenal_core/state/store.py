"""SQLite(WAL) 영속 상태 저장소. 상태는 절대 메모리에만 두지 않는다.

핵심 불변식: "sink 쓰기 성공 → done 마킹" 순서 (docs/02-architecture.md).
단위 지표(row_count/byte_count/duration_ms)는 비용 가시성의 원료 — P0부터 기록 (docs/07).
구현: docs/plans/2026-07-08-m1-core-foundation.md Task 3
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arsenal_core.identity import unit_id as make_unit_id

# cursor/link 트래버설이 next_cursor=None에 도달해 "완료"됐음을 나타내는 예약 센티널.
# cursors 테이블(schema 불변)에 실제 커서/URL 값처럼 저장된다 — NUL 바이트 프리픽스라
# 실제 커서 값이나 URL과 절대 충돌하지 않는다. M2-B: 재실행 무한루프 수정.
SOURCE_EXHAUSTED = "\x00__ARSENAL_SOURCE_EXHAUSTED__"

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


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
        return cls(
            unit_id=make_unit_id(pipeline, source, unit_key),
            pipeline=pipeline,
            unit_key=unit_key,
            payload=payload,
        )


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
    (재시도는 with_retry가 mark_failed 이전에 in-process로 수행한다 — 이 저장소는
    running → pending으로 되돌리는 전이를 갖지 않는다.)
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def register(self, unit: UnitSpec) -> None:
        """INSERT OR IGNORE — 두 번 등록해도 안전 (멱등)."""
        self._conn.execute(
            "INSERT OR IGNORE INTO units "
            "(unit_id, pipeline, unit_key, payload, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (unit.unit_id, unit.pipeline, unit.unit_key, json.dumps(unit.payload), _now(), _now()),
        )
        self._conn.commit()

    def _set_status(self, uid: str, status: str, error: str | None = None) -> None:
        bump = 1 if status == "failed" else 0
        self._conn.execute(
            "UPDATE units SET status=?, last_error=?, attempts=attempts+?, updated_at=? "
            "WHERE unit_id=?",
            (status, error, bump, _now(), uid),
        )
        self._conn.commit()

    def mark_running(self, uid: str) -> None:
        self._set_status(uid, "running")

    def mark_done(
        self,
        uid: str,
        *,
        row_count: int | None = None,
        byte_count: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE units SET status='done', row_count=?, byte_count=?, duration_ms=?, "
            "updated_at=? WHERE unit_id=?",
            (row_count, byte_count, duration_ms, _now(), uid),
        )
        self._conn.commit()

    def mark_done_and_advance_cursor(
        self,
        uid: str,
        *,
        pipeline: str,
        source: str,
        cursor: str,
        row_count: int | None = None,
        byte_count: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """mark_done + set_cursor를 한 커넥션·한 커밋으로 묶는다 (M2 최종 리뷰 FIX 1).

        두 문장을 별개 트랜잭션(mark_done() 다음 set_cursor())으로 실행하면, 그
        사이에 크래시가 나면 unit은 done인데 커서는 그 unit을 만들어낸 이전 값에
        멈춘 상태로 남는다. 재실행 시 그 스테일 커서로 seed된 RestSource가 이미
        done인 그 unit을 다시 내고, runner의 is_done 스킵 경로는 fetch()를 부르지
        않으니 커서가 영원히 전진하지 못해 무한루프가 된다 — cursor/link 모드의
        모든 중간 페이지에 영향을 준다(마지막 페이지의 SOURCE_EXHAUSTED 센티널만
        고쳤던 이전 수정으로는 부족했다). 두 UPDATE/INSERT를 커밋 하나로 묶어,
        크래시가 나면 "둘 다 반영" 또는 "둘 다 미반영"만 관측되게 한다 — 이 둘
        사이의 상태는 존재하지 않는다.
        """
        self._conn.execute(
            "UPDATE units SET status='done', row_count=?, byte_count=?, duration_ms=?, "
            "updated_at=? WHERE unit_id=?",
            (row_count, byte_count, duration_ms, _now(), uid),
        )
        self._conn.execute(
            "INSERT INTO cursors (pipeline, source, cursor, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(pipeline, source) DO UPDATE SET cursor=excluded.cursor, "
            "updated_at=excluded.updated_at",
            (pipeline, source, cursor, _now()),
        )
        self._conn.commit()

    def mark_failed(self, uid: str, error: str) -> None:
        """attempts를 1 올리고 last_error 기록."""
        self._set_status(uid, "failed", error)

    def mark_quarantined(self, uid: str, reason: str) -> None:
        """검증 위반으로 격리. _set_status를 재사용하지 않는 이유: quarantine은
        재시도 실패가 아니라서 attempts를 올리면 안 된다 (M2-E)."""
        self._conn.execute(
            "UPDATE units SET status='quarantined', last_error=?, updated_at=? WHERE unit_id=?",
            (reason, _now(), uid),
        )
        self._conn.commit()

    def quarantined(self, pipeline: str) -> list[UnitRecord]:
        """`pugio dlq list`의 데이터 — 격리된 unit만."""
        rows = self._conn.execute(
            "SELECT unit_id, status, attempts, last_error FROM units "
            "WHERE pipeline=? AND status='quarantined'",
            (pipeline,),
        ).fetchall()
        return [UnitRecord(*r) for r in rows]

    def requeue(self, uid: str) -> int:
        """`pugio dlq retry` — 격리를 풀고 pending으로 되돌려 다음 run이 재수집하게 한다.

        반환값은 실제로 갱신된 행 수 (0 또는 1) — 존재하지 않는 unit_id를 넘기면
        조용히 0건 갱신되고도 성공한 것처럼 보이는 걸 막으려고 CLI가 이 값을 확인한다
        (M2-E FIX 1: unit_key/unit_id 혼동으로 인한 silent no-op false success).
        """
        cur = self._conn.execute(
            "UPDATE units SET status='pending', last_error=NULL, updated_at=? WHERE unit_id=?",
            (_now(), uid),
        )
        self._conn.commit()
        return cur.rowcount

    def status(self, uid: str) -> str | None:
        row = self._conn.execute("SELECT status FROM units WHERE unit_id=?", (uid,)).fetchone()
        return row[0] if row else None

    def is_done(self, uid: str) -> bool:
        return self.status(uid) == "done"

    def get(self, uid: str) -> UnitRecord:
        row = self._conn.execute(
            "SELECT unit_id, status, attempts, last_error FROM units WHERE unit_id=?", (uid,)
        ).fetchone()
        if row is None:
            raise KeyError(uid)
        return UnitRecord(*row)

    def metrics(self, uid: str) -> UnitMetrics:
        row = self._conn.execute(
            "SELECT row_count, byte_count, duration_ms FROM units WHERE unit_id=?", (uid,)
        ).fetchone()
        if row is None:
            raise KeyError(uid)
        return UnitMetrics(*row)

    def get_cursor(self, pipeline: str, source: str) -> str | None:
        """cursor/link 모드 재개용 — 없으면 None (첫 실행)."""
        row = self._conn.execute(
            "SELECT cursor FROM cursors WHERE pipeline=? AND source=?", (pipeline, source)
        ).fetchone()
        return row[0] if row else None

    def set_cursor(self, pipeline: str, source: str, cursor: str) -> None:
        """upsert — 같은 (pipeline, source)는 최신 커서로 덮어쓴다."""
        self._conn.execute(
            "INSERT INTO cursors (pipeline, source, cursor, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(pipeline, source) DO UPDATE SET cursor=excluded.cursor, "
            "updated_at=excluded.updated_at",
            (pipeline, source, cursor, _now()),
        )
        self._conn.commit()

    def last_schema(self, pipeline: str) -> str | None:
        """이 pipeline의 가장 최근 스키마 스냅샷 — 없으면 None (첫 실행)."""
        row = self._conn.execute(
            "SELECT schema_json FROM schema_snapshots WHERE pipeline=? "
            "ORDER BY captured_at DESC, rowid DESC LIMIT 1",
            (pipeline,),
        ).fetchone()
        return row[0] if row else None

    def snapshot_schema(self, pipeline: str, schema_json: str) -> None:
        """가장 최근 스냅샷과 다를 때만 append — 스키마가 안 바뀌었으면 no-op.

        스키마 변경 이력만 남기려는 것 — 매 run마다 동일한 schema_json을 계속
        쌓으면 감사 이력이 노이즈로 뒤덮인다 (탐지/정책은 P1, 여기는 기록만).
        """
        if self.last_schema(pipeline) == schema_json:
            return
        self._conn.execute(
            "INSERT INTO schema_snapshots (pipeline, captured_at, schema_json) VALUES (?, ?, ?)",
            (pipeline, _now(), schema_json),
        )
        self._conn.commit()

    def counts(self, pipeline: str) -> dict[str, int]:
        """status → 개수. `pugio status`의 데이터."""
        rows = self._conn.execute(
            "SELECT status, count(*) FROM units WHERE pipeline=? GROUP BY status", (pipeline,)
        ).fetchall()
        return dict(rows)

    def close(self) -> None:
        self._conn.close()
