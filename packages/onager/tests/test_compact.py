from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from onager.compact import compact_dataset, plan_compaction

from arsenal_core.errors import FatalError


def write_part(path: Path, value: int) -> None:
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        pa.table({"id": [value], "value": [value * 10]}), path
    )


def test_dry_run_selects_small_files_without_mutating_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "data"
    dataset.mkdir()
    write_part(dataset / "a.parquet", 1)
    write_part(dataset / "b.parquet", 2)
    before = sorted(path.name for path in dataset.glob("*.parquet"))

    report = compact_dataset(dataset, target_bytes=10_000, dry_run=True)

    assert report.compacted is False
    assert report.selected_files == 2
    assert sorted(path.name for path in dataset.glob("*.parquet")) == before


def test_compaction_replaces_candidates_and_preserves_rows(tmp_path: Path) -> None:
    dataset = tmp_path / "data"
    dataset.mkdir()
    write_part(dataset / "a.parquet", 1)
    write_part(dataset / "b.parquet", 2)

    report = compact_dataset(dataset, target_bytes=10_000)

    assert report.compacted is True
    files = sorted(dataset.glob("*.parquet"))
    assert len(files) == 1
    table = pq.read_table(files[0])  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    assert sorted(table.column("id").to_pylist()) == [1, 2]  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


def test_plan_with_too_few_files_is_a_noop(tmp_path: Path) -> None:
    dataset = tmp_path / "data"
    dataset.mkdir()
    write_part(dataset / "a.parquet", 1)

    plan = plan_compaction(dataset, target_bytes=10_000)
    report = compact_dataset(dataset, target_bytes=10_000)

    assert plan.selected is False
    assert report.compacted is False


def test_max_input_bytes_is_a_safety_cutoff_and_logs_execution(tmp_path: Path) -> None:
    dataset = tmp_path / "data"
    dataset.mkdir()
    write_part(dataset / "a.parquet", 1)
    write_part(dataset / "b.parquet", 2)

    report = compact_dataset(dataset, target_bytes=10_000, max_input_bytes=1, dry_run=True)

    assert report.compacted is False
    log = (tmp_path / ".onager" / "compaction-log.jsonl").read_text(encoding="utf-8")
    assert '"max_input_bytes": 1' in log


def test_remote_and_invalid_options_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="local"):
        plan_compaction(Path("s3://bucket/data"))
    with pytest.raises(FatalError, match="at least 2"):
        plan_compaction(tmp_path, min_files=1)
    with pytest.raises(FatalError, match="max_input_bytes"):
        plan_compaction(tmp_path, max_input_bytes=0)
