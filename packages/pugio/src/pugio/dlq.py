"""DLQ(dead-letter queue) 파일 저장소 — 격리된 unit의 batch + 사유를 evidence로 남긴다.

M2-E: DLQ 파일은 재적재 소스가 아니다 — retry는 실제 소스에서 다시 fetch한다
(docs/01-scope.md M2 Task 2.7~2.8). `.arsenal/dlq/{pipeline}/{unit_id}.parquet`
+ `{unit_id}.json`(사유) 쌍으로 저장한다.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from arsenal_core.state import UnitSpec
from pugio.validate.gate import Violation


def dlq_dir(state_dir: Path, pipeline: str) -> Path:
    return Path(state_dir) / "dlq" / pipeline


def write_dlq(
    state_dir: Path,
    pipeline: str,
    unit: UnitSpec,
    batch: pa.RecordBatch,
    violations: list[Violation],
) -> Path:
    """격리 batch를 parquet으로, 위반 사유를 같은 이름의 json으로 저장한다."""
    d = dlq_dir(state_dir, pipeline)
    d.mkdir(parents=True, exist_ok=True)
    parquet_path = d / f"{unit.unit_id}.parquet"
    pq.write_table(pa.Table.from_batches([batch]), parquet_path)  # pyright: ignore[reportUnknownMemberType]
    reason = {
        "unit_id": unit.unit_id,
        "unit_key": unit.unit_key,
        "violations": [{"rule": v.rule, "field": v.field, "count": v.count} for v in violations],
        "quarantined_at": datetime.now(UTC).isoformat(),
    }
    (d / f"{unit.unit_id}.json").write_text(
        json.dumps(reason, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return parquet_path


def read_reason(json_path: Path) -> dict[str, object]:
    return json.loads(json_path.read_text(encoding="utf-8"))


def remove_dlq(state_dir: Path, pipeline: str, unit_id: str) -> None:
    """parquet + json 둘 다 삭제 — 없으면 조용히 넘어간다 (재호출 안전)."""
    d = dlq_dir(state_dir, pipeline)
    for suffix in (".parquet", ".json"):
        path = d / f"{unit_id}{suffix}"
        path.unlink(missing_ok=True)
