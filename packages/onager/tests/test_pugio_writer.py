"""Onager compaction and a live Pugio run fence each other (same as Hasta streams)."""

from __future__ import annotations

from pathlib import Path

import pytest
from onager.compact import compact_dataset

from arsenal_core.spec.models import FileSourceSpec, ParquetSinkSpec, PipelineSpec
from arsenal_core.writers import DatasetBusyError
from pugio.runner import run_pipeline


def pipeline(tmp_path: Path) -> PipelineSpec:
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.csv").write_text("id\n1\n", encoding="utf-8")
    return PipelineSpec(
        name="orders",
        state_dir=str(tmp_path / ".arsenal"),
        source=FileSourceSpec(type="file", path=str(tmp_path / "*.csv")),
        sink=ParquetSinkSpec(type="parquet", path=str(tmp_path / "out")),
    )


def test_compaction_refuses_while_a_pugio_run_is_live(tmp_path: Path) -> None:
    spec = pipeline(tmp_path)
    outcome: list[str] = []

    def compact_mid_run(done: int) -> None:
        if done == 2:
            with pytest.raises(DatasetBusyError, match="pipeline:orders|orders"):
                compact_dataset(tmp_path / "out", target_bytes=1 << 30, min_files=2)
            outcome.append("refused")

    run_pipeline(spec, on_unit_complete=compact_mid_run)

    assert outcome == ["refused"]
    assert len(list((tmp_path / "out").glob("*.parquet"))) == 3  # nothing was rewritten


def test_compaction_runs_once_the_pugio_run_is_over(tmp_path: Path) -> None:
    run_pipeline(pipeline(tmp_path))

    report = compact_dataset(tmp_path / "out", target_bytes=1 << 30, min_files=2)

    assert report is not None
    assert len(list((tmp_path / "out").glob("*.parquet"))) == 1
