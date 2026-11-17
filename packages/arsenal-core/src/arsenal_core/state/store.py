"""SQLite(WAL) 영속 상태 저장소. 상태는 절대 메모리에만 두지 않는다.

핵심 불변식: "sink 쓰기 성공 → done 마킹" 순서 (docs/02-architecture.md).
단위 지표(row_count/byte_count/duration_ms)는 비용 가시성의 원료 — P0부터 기록 (docs/07).
구현: docs/plans/2026-07-08-m1-core-foundation.md Task 3
"""

import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from arsenal_core.execution import LeaseToken, OutputProof, canonical_json
from arsenal_core.identity import unit_id as make_unit_id
from arsenal_core.state.migrations import CURRENT_SCHEMA_VERSION, apply_migrations

# cursor/link 트래버설이 next_cursor=None에 도달해 "완료"됐음을 나타내는 예약 센티널.
SOURCE_EXHAUSTED = "\x00__ARSENAL_SOURCE_EXHAUSTED__"


def _utcnow() -> datetime:
    return datetime.now(UTC)


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


@dataclass(frozen=True)
class PipelineMetrics:
    """Cumulative completed-unit metrics used by Scorpio/status reporting."""

    completed_units: int
    row_count: int
    byte_count: int
    duration_ms: int
    last_completed_at: str | None


@dataclass(frozen=True)
class LineageRecord:
    pipeline: str
    source_type: str
    source_ref: str
    sink_type: str
    sink_ref: str
    updated_at: str


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    pipeline: str
    revision: str
    mode: str
    executor: str
    status: str
    attempt: int
    external_job: Mapping[str, Any] | None
    error: str | None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class StreamCheckpoint:
    pipeline: str
    stream_name: str
    generation: int
    position: Mapping[str, Any]
    watermark: str | None
    last_batch_id: str | None
    updated_at: str | None = None


@dataclass(frozen=True)
class StreamBatch:
    batch_id: str
    pipeline: str
    stream_name: str
    revision: str
    start_position: Mapping[str, Any]
    end_position: Mapping[str, Any]
    event_hash: str
    status: str
    output_proof: Mapping[str, Any] | None
    rows: int | None
    bytes: int | None
    late_rows: int
    error: str | None
    generation: int = 0


@dataclass(frozen=True)
class LeaseRecord:
    """Read-only view of who holds a resource lease and until when."""

    resource: str
    owner_id: str
    fence: int
    expires_at: datetime
    updated_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at <= now


@dataclass(frozen=True)
class StreamBatchSummary:
    """Ledger totals for one stream; rows/bytes count committed batches only."""

    by_status: Mapping[str, int]
    rows: int
    bytes: int
    late_rows: int


class LeaseLostError(RuntimeError):
    """Raised when a stale or expired lease tries to mutate durable state."""


class StateStore:
    """unit 상태·커서·스키마 스냅샷의 영속 저장소.

    상태 전이: pending → running → done | failed | quarantined
    (재시도는 with_retry가 mark_failed 이전에 in-process로 수행한다 — 이 저장소는
    running → pending으로 되돌리는 전이를 갖지 않는다.)
    """

    def __init__(
        self,
        db_path: Path,
        *,
        backup_dir: Path | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._db_path = Path(db_path)
        self._clock = clock
        self._backup_dir = backup_dir
        existed = self._db_path.exists() and self._db_path.stat().st_size > 0
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        backed_up = False

        def backup(version: int) -> None:
            nonlocal backed_up
            if existed and not backed_up:
                self._backup_database(version)
                backed_up = True

        apply_migrations(self._conn, before_migration=backup)

    @classmethod
    def open(cls, db_path: Path, **kwargs: Any) -> "StateStore":
        """Explicit constructor alias for callers that want a named open operation."""
        return cls(db_path, **kwargs)

    @property
    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def _backup_database(self, version: int) -> Path:
        backup_dir = self._backup_dir or self._db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = self._clock().astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = backup_dir / f"{self._db_path.stem}-v{version}-{stamp}.sqlite"
        with sqlite3.connect(target) as destination:
            self._conn.backup(destination)
        return target

    def migrate(self, *, dry_run: bool = False, backup_dir: Path | None = None) -> int:
        """Apply pending migrations; ``dry_run`` reports the target without writing."""
        version = self.schema_version
        if version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError("state database is newer than this arsenal-core version")
        if dry_run:
            return CURRENT_SCHEMA_VERSION
        previous_backup_dir = self._backup_dir
        if backup_dir is not None:
            self._backup_dir = backup_dir
        try:
            apply_migrations(self._conn, before_migration=self._backup_before_migration)
        finally:
            self._backup_dir = previous_backup_dir
        return self.schema_version

    def _backup_before_migration(self, version: int) -> None:
        self._backup_database(version)

    def _now(self) -> str:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("StateStore clock must return a timezone-aware datetime")
        return value.astimezone(UTC).isoformat()

    def register(self, unit: UnitSpec) -> None:
        """INSERT OR IGNORE — 두 번 등록해도 안전 (멱등)."""
        self._conn.execute(
            "INSERT OR IGNORE INTO units "
            "(unit_id, pipeline, unit_key, payload, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (
                unit.unit_id,
                unit.pipeline,
                unit.unit_key,
                json.dumps(unit.payload),
                self._now(),
                self._now(),
            ),
        )
        self._conn.commit()

    def _set_status(self, uid: str, status: str, error: str | None = None) -> None:
        bump = 1 if status == "failed" else 0
        self._conn.execute(
            "UPDATE units SET status=?, last_error=?, attempts=attempts+?, updated_at=? "
            "WHERE unit_id=?",
            (status, error, bump, self._now(), uid),
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
            (row_count, byte_count, duration_ms, self._now(), uid),
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
            (row_count, byte_count, duration_ms, self._now(), uid),
        )
        self._conn.execute(
            "INSERT INTO cursors (pipeline, source, cursor, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(pipeline, source) DO UPDATE SET cursor=excluded.cursor, "
            "updated_at=excluded.updated_at",
            (pipeline, source, cursor, self._now()),
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
            (reason, self._now(), uid),
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
            (self._now(), uid),
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
            (pipeline, source, cursor, self._now()),
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
            (pipeline, self._now(), schema_json),
        )
        self._conn.commit()

    def counts(self, pipeline: str) -> dict[str, int]:
        """status → 개수. `pugio status`의 데이터."""
        rows = self._conn.execute(
            "SELECT status, count(*) FROM units WHERE pipeline=? GROUP BY status", (pipeline,)
        ).fetchall()
        return dict(rows)

    def pipeline_metrics(self, pipeline: str) -> PipelineMetrics:
        """Return cumulative rows/bytes/time and freshness for completed units."""
        row = self._conn.execute(
            "SELECT count(*), COALESCE(sum(row_count), 0), COALESCE(sum(byte_count), 0), "
            "COALESCE(sum(duration_ms), 0), max(updated_at) FROM units "
            "WHERE pipeline=? AND status='done'",
            (pipeline,),
        ).fetchone()
        if row is None:  # pragma: no cover - aggregate queries always return one row
            return PipelineMetrics(0, 0, 0, 0, None)
        return PipelineMetrics(
            completed_units=int(row[0]),
            row_count=int(row[1]),
            byte_count=int(row[2]),
            duration_ms=int(row[3]),
            last_completed_at=row[4],
        )

    def record_lineage(
        self,
        pipeline: str,
        *,
        source_type: str,
        source_ref: str,
        sink_type: str,
        sink_ref: str,
    ) -> None:
        """Upsert the latest source-to-sink edge observed by a pipeline run."""
        self._conn.execute(
            "INSERT INTO lineage "
            "(pipeline, source_type, source_ref, sink_type, sink_ref, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(pipeline) DO UPDATE SET source_type=excluded.source_type, "
            "source_ref=excluded.source_ref, sink_type=excluded.sink_type, "
            "sink_ref=excluded.sink_ref, updated_at=excluded.updated_at",
            (pipeline, source_type, source_ref, sink_type, sink_ref, self._now()),
        )
        self._conn.commit()

    def lineage(self, pipeline: str) -> LineageRecord | None:
        row = self._conn.execute(
            "SELECT pipeline, source_type, source_ref, sink_type, sink_ref, updated_at "
            "FROM lineage WHERE pipeline=?",
            (pipeline,),
        ).fetchone()
        return LineageRecord(*row) if row else None

    # P2 state APIs.  They are deliberately separate from the v0.2 unit APIs
    # above so existing collection callers retain their established semantics.
    def acquire_lease(self, resource: str, owner_id: str, ttl_seconds: float) -> LeaseToken | None:
        """Atomically acquire an expired resource lease and advance its fence.

        ``None`` means another owner still holds an unexpired lease. A caller
        that already owns it may reacquire it, refreshing expiry but preserving
        its fencing value.
        """
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        now = self._clock().astimezone(UTC)
        expires = now + timedelta(seconds=ttl_seconds)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT owner_id, fence, expires_at FROM leases WHERE resource=?", (resource,)
            ).fetchone()
            if row is not None and row[0] != owner_id and row[2] > now.isoformat():
                self._conn.rollback()
                return None
            if row is None:
                fence = 1
            elif row[0] == owner_id:
                fence = int(row[1])
            else:
                fence = int(row[1]) + 1
            self._conn.execute(
                "INSERT INTO leases(resource, owner_id, fence, expires_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(resource) DO UPDATE SET owner_id=excluded.owner_id, "
                "fence=excluded.fence, "
                "expires_at=excluded.expires_at, updated_at=excluded.updated_at",
                (resource, owner_id, fence, expires.isoformat(), now.isoformat()),
            )
            self._conn.commit()
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise
        return LeaseToken(resource=resource, owner_id=owner_id, fence=fence, expires_at=expires)

    def renew_lease(self, token: LeaseToken, ttl_seconds: float) -> LeaseToken | None:
        """Refresh an active lease, returning ``None`` if it was fenced out."""
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        now = self._clock().astimezone(UTC)
        expires = now + timedelta(seconds=ttl_seconds)
        cur = self._conn.execute(
            "UPDATE leases SET expires_at=?, updated_at=? WHERE resource=? "
            "AND owner_id=? AND fence=? "
            "AND expires_at > ?",
            (
                expires.isoformat(),
                now.isoformat(),
                token.resource,
                token.owner_id,
                token.fence,
                now.isoformat(),
            ),
        )
        self._conn.commit()
        if cur.rowcount != 1:
            return None
        return LeaseToken(token.resource, token.owner_id, token.fence, expires)

    def release_lease(self, token: LeaseToken) -> bool:
        """Release only the exact active ownership generation."""
        cur = self._conn.execute(
            "DELETE FROM leases WHERE resource=? AND owner_id=? AND fence=?",
            (token.resource, token.owner_id, token.fence),
        )
        self._conn.commit()
        return cur.rowcount == 1

    def lease(self, resource: str) -> LeaseRecord | None:
        row = self._conn.execute(
            "SELECT resource, owner_id, fence, expires_at, updated_at FROM leases WHERE resource=?",
            (resource,),
        ).fetchone()
        if row is None:
            return None
        return LeaseRecord(
            resource=row[0],
            owner_id=row[1],
            fence=int(row[2]),
            expires_at=datetime.fromisoformat(row[3]),
            updated_at=datetime.fromisoformat(row[4]),
        )

    def _assert_active_lease(self, token: LeaseToken) -> None:
        now = self._now()
        row = self._conn.execute(
            "SELECT 1 FROM leases WHERE resource=? AND owner_id=? AND fence=? AND expires_at > ?",
            (token.resource, token.owner_id, token.fence, now),
        ).fetchone()
        if row is None:
            raise LeaseLostError(f"lease lost for {token.resource!r} (fence {token.fence})")

    def create_run(
        self,
        *,
        run_id: str,
        pipeline: str,
        revision: str,
        mode: str,
        executor: str,
        guarantees: Mapping[str, Any],
    ) -> None:
        """Insert a planned run idempotently before submitting work externally."""
        now = self._now()
        self._conn.execute(
            "INSERT OR IGNORE INTO runs(run_id, pipeline, revision, mode, executor, status, "
            "guarantee_json, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'planned', ?, ?, ?)",
            (run_id, pipeline, revision, mode, executor, canonical_json(guarantees), now, now),
        )
        self._conn.commit()

    _RUN_COLUMNS = (
        "run_id, pipeline, revision, mode, executor, status, attempt, "
        "external_job_json, error, created_at, updated_at"
    )

    def run(self, run_id: str) -> RunRecord | None:
        row = self._conn.execute(
            f"SELECT {self._RUN_COLUMNS} FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return None if row is None else self._run_record(row)

    def runs(self, *, pipeline: str | None = None) -> list[RunRecord]:
        """All runs, oldest first, optionally for one pipeline (for monitoring)."""
        if pipeline is None:
            rows = self._conn.execute(
                f"SELECT {self._RUN_COLUMNS} FROM runs ORDER BY created_at, run_id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT {self._RUN_COLUMNS} FROM runs WHERE pipeline=? "
                "ORDER BY created_at, run_id",
                (pipeline,),
            ).fetchall()
        return [self._run_record(row) for row in rows]

    @staticmethod
    def _run_record(row: Any) -> RunRecord:
        return RunRecord(
            run_id=row[0],
            pipeline=row[1],
            revision=row[2],
            mode=row[3],
            executor=row[4],
            status=row[5],
            attempt=row[6],
            external_job=json.loads(row[7]) if row[7] else None,
            error=row[8],
            created_at=row[9],
            updated_at=row[10],
        )

    def update_run(
        self,
        run_id: str,
        *,
        status: str,
        lease: LeaseToken,
        external_job: Mapping[str, Any] | None = None,
        error: str | None = None,
        increment_attempt: bool = False,
    ) -> None:
        """Fenced run state update; stale coordinators cannot report success."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._assert_active_lease(lease)
            cur = self._conn.execute(
                "UPDATE runs SET status=?, external_job_json=?, error=?, attempt=attempt+?, "
                "updated_at=? "
                "WHERE run_id=?",
                (
                    status,
                    canonical_json(external_job) if external_job is not None else None,
                    error,
                    1 if increment_attempt else 0,
                    self._now(),
                    run_id,
                ),
            )
            if cur.rowcount != 1:
                raise KeyError(run_id)
            self._conn.commit()
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def checkpoint(self, pipeline: str, stream_name: str) -> StreamCheckpoint | None:
        row = self._conn.execute(
            "SELECT pipeline, stream_name, generation, position_json, watermark, last_batch_id, "
            "updated_at FROM stream_checkpoints WHERE pipeline=? AND stream_name=?",
            (pipeline, stream_name),
        ).fetchone()
        if row is None:
            return None
        return StreamCheckpoint(
            pipeline=row[0],
            stream_name=row[1],
            generation=row[2],
            position=json.loads(row[3]),
            watermark=row[4],
            last_batch_id=row[5],
            updated_at=row[6],
        )

    def plan_stream_batch(
        self,
        *,
        batch_id: str,
        pipeline: str,
        stream_name: str,
        revision: str,
        start_position: Mapping[str, Any],
        end_position: Mapping[str, Any],
        event_hash: str,
        generation: int = 0,
    ) -> StreamBatch:
        """Record a deterministic batch identity without changing its checkpoint."""
        start_json, end_json = canonical_json(start_position), canonical_json(end_position)
        now = self._now()
        self._conn.execute(
            "INSERT OR IGNORE INTO stream_batches(batch_id, pipeline, stream_name, revision, "
            "generation, start_position_json, end_position_json, event_hash, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?)",
            (
                batch_id,
                pipeline,
                stream_name,
                revision,
                generation,
                start_json,
                end_json,
                event_hash,
                now,
                now,
            ),
        )
        self._conn.commit()
        batch = self.stream_batch(batch_id)
        if batch is None:
            raise ValueError(
                f"batch_id {batch_id!r} conflicts with an existing batch for the same "
                "source range and generation"
            )
        identity = (
            batch.pipeline,
            batch.stream_name,
            batch.revision,
            batch.generation,
            batch.start_position,
            batch.end_position,
        )
        expected_identity = (
            pipeline,
            stream_name,
            revision,
            generation,
            json.loads(start_json),
            json.loads(end_json),
        )
        if identity != expected_identity:
            raise ValueError(f"batch_id {batch_id!r} was reused for a different batch")
        return batch

    def stream_batch(self, batch_id: str) -> StreamBatch | None:
        row = self._conn.execute(
            "SELECT batch_id, pipeline, stream_name, revision, start_position_json, "
            "end_position_json, event_hash, status, output_ref_json, rows, bytes, late_rows, "
            "error, generation FROM stream_batches "
            "WHERE batch_id=?",
            (batch_id,),
        ).fetchone()
        if row is None:
            return None
        return StreamBatch(
            batch_id=row[0],
            pipeline=row[1],
            stream_name=row[2],
            revision=row[3],
            start_position=json.loads(row[4]),
            end_position=json.loads(row[5]),
            event_hash=row[6],
            status=row[7],
            output_proof=json.loads(row[8]) if row[8] else None,
            rows=row[9],
            bytes=row[10],
            late_rows=row[11],
            error=row[12],
            generation=int(row[13]),
        )

    def commit_stream_batch(
        self,
        batch_id: str,
        *,
        lease: LeaseToken,
        checkpoint_position: Mapping[str, Any],
        generation: int = 0,
        watermark: str | None = None,
        output_proof: OutputProof | Mapping[str, Any],
        rows: int | None = None,
        bytes: int | None = None,
        late_rows: int = 0,
    ) -> None:
        """Atomically commit output proof, batch ledger, and checkpoint under a fence."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._assert_active_lease(lease)
            batch = self.stream_batch(batch_id)
            if batch is None:
                raise KeyError(batch_id)
            if batch.status == "committed":
                self._conn.commit()
                return
            if batch.status not in {"planned", "claimed", "running"}:
                raise ValueError(f"cannot commit stream batch in {batch.status!r} state")
            now = self._now()
            self._conn.execute(
                "UPDATE stream_batches SET status='committed', output_ref_json=?, rows=?, bytes=?, "
                "late_rows=?, error=NULL, updated_at=? WHERE batch_id=?",
                (canonical_json(output_proof), rows, bytes, late_rows, now, batch_id),
            )
            self._conn.execute(
                "INSERT INTO stream_checkpoints(pipeline, stream_name, generation, position_json, "
                "watermark, "
                "last_batch_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(pipeline, stream_name) DO UPDATE SET generation=excluded.generation, "
                "position_json=excluded.position_json, watermark=excluded.watermark, "
                "last_batch_id=excluded.last_batch_id, updated_at=excluded.updated_at",
                (
                    batch.pipeline,
                    batch.stream_name,
                    generation,
                    canonical_json(checkpoint_position),
                    watermark,
                    batch_id,
                    now,
                ),
            )
            self._conn.commit()
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def stream_batch_summary(self, pipeline: str, stream_name: str) -> StreamBatchSummary:
        counts = self._conn.execute(
            "SELECT status, COUNT(*) FROM stream_batches WHERE pipeline=? AND stream_name=? "
            "GROUP BY status",
            (pipeline, stream_name),
        ).fetchall()
        totals = self._conn.execute(
            "SELECT COALESCE(SUM(rows), 0), COALESCE(SUM(bytes), 0), COALESCE(SUM(late_rows), 0) "
            "FROM stream_batches WHERE pipeline=? AND stream_name=? AND status='committed'",
            (pipeline, stream_name),
        ).fetchone()
        return StreamBatchSummary(
            by_status={str(status): int(count) for status, count in counts},
            rows=int(totals[0]),
            bytes=int(totals[1]),
            late_rows=int(totals[2]),
        )

    def reset_checkpoint(
        self, pipeline: str, stream_name: str, *, lease: LeaseToken
    ) -> StreamCheckpoint:
        """Start a new checkpoint generation from the source's beginning.

        The batch ledger is kept for audit. Callers fold the generation into
        batch identities, so the replay produces new batches instead of being
        skipped as already committed. Fenced like every checkpoint mutation.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._assert_active_lease(lease)
            current = self.checkpoint(pipeline, stream_name)
            generation = (current.generation if current is not None else 0) + 1
            self._conn.execute(
                "INSERT INTO stream_checkpoints(pipeline, stream_name, generation, position_json, "
                "watermark, last_batch_id, updated_at) VALUES (?, ?, ?, ?, NULL, NULL, ?) "
                "ON CONFLICT(pipeline, stream_name) DO UPDATE SET generation=excluded.generation, "
                "position_json=excluded.position_json, watermark=NULL, last_batch_id=NULL, "
                "updated_at=excluded.updated_at",
                (pipeline, stream_name, generation, canonical_json({}), self._now()),
            )
            self._conn.commit()
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise
        reset = self.checkpoint(pipeline, stream_name)
        if reset is None:  # pragma: no cover - database corruption guard
            raise RuntimeError(f"failed to reset checkpoint for {pipeline}/{stream_name}")
        return reset

    def fail_stream_batch(self, batch_id: str, *, lease: LeaseToken, error: str) -> None:
        """Fenced failure recording that leaves the checkpoint unchanged."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._assert_active_lease(lease)
            cur = self._conn.execute(
                "UPDATE stream_batches SET status='failed', error=?, updated_at=? WHERE batch_id=? "
                "AND status != 'committed'",
                (error, self._now(), batch_id),
            )
            if cur.rowcount != 1:
                raise KeyError(batch_id)
            self._conn.commit()
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def close(self) -> None:
        self._conn.close()
