from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from onager import promote_backfill, run_backfill

from arsenal_core.errors import FatalError


def write_part(path: Path, value: int) -> None:
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        pa.table({"id": [value]}), path
    )


def test_backfill_isolated_until_explicit_promotion(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    write_part(dataset / "part.parquet", 1)

    def transform(source: Path, output: Path) -> None:
        write_part(output / "part.parquet", 2)
        assert (source / "part.parquet").exists()

    workspace = run_backfill(dataset, "run-1", transform)
    assert workspace.status == "ready"
    assert pq.read_table(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        dataset / "part.parquet"
    ).column("id").to_pylist() == [1]  # pyright: ignore[reportUnknownMemberType]
    assert (
        (workspace.root / "status.json").read_text(encoding="utf-8").strip()
        == '{"status": "ready"}'
    )

    promote_backfill(workspace, dataset)
    assert pq.read_table(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        dataset / "part.parquet"
    ).column("id").to_pylist() == [2]  # pyright: ignore[reportUnknownMemberType]
    assert '"promoted"' in (workspace.root / "status.json").read_text(encoding="utf-8")


def test_failed_backfill_keeps_failure_evidence_and_active_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    write_part(dataset / "part.parquet", 1)

    def fail(_: Path, __: Path) -> None:
        raise RuntimeError("bad transform")

    with pytest.raises(RuntimeError, match="bad transform"):
        run_backfill(dataset, "failed", fail)
    status = tmp_path / ".onager" / "backfills" / "failed" / "status.json"
    assert '"failed"' in status.read_text(encoding="utf-8")
    assert pq.read_table(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        dataset / "part.parquet"
    ).column("id").to_pylist() == [1]  # pyright: ignore[reportUnknownMemberType]


def test_backfill_rejects_duplicate_or_unsafe_run(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    with pytest.raises(FatalError, match="path-safe"):
        run_backfill(dataset, "../escape", lambda _source, _output: None)
