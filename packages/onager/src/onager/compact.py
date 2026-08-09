"""Small-file compaction with dry-run and recoverable directory replacement."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import duckdb
from scutum import FileLock

from arsenal_core.errors import FatalError


@dataclass(frozen=True)
class CompactionPlan:
    dataset: Path
    candidates: tuple[Path, ...]
    preserved: tuple[Path, ...]
    target_bytes: int
    min_files: int
    max_input_bytes: int | None

    @property
    def selected(self) -> bool:
        return len(self.candidates) >= self.min_files and (
            self.max_input_bytes is None or self.input_bytes <= self.max_input_bytes
        )

    @property
    def input_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.candidates)


@dataclass(frozen=True)
class CompactionReport:
    dataset: Path
    dry_run: bool
    selected_files: int
    preserved_files: int
    input_bytes: int
    output_bytes: int
    compacted: bool


def plan_compaction(
    dataset: Path,
    *,
    target_bytes: int = 128 * 1024 * 1024,
    min_files: int = 2,
    max_input_bytes: int | None = None,
) -> CompactionPlan:
    """Select local Parquet files smaller than ``target_bytes``."""
    _validate_options(dataset, target_bytes, min_files, max_input_bytes)
    files = tuple(sorted(path for path in dataset.rglob("*.parquet") if path.is_file()))
    candidates = tuple(path for path in files if path.stat().st_size < target_bytes)
    preserved = tuple(path for path in files if path not in candidates)
    return CompactionPlan(dataset, candidates, preserved, target_bytes, min_files, max_input_bytes)


def compact_dataset(
    dataset: Path,
    *,
    target_bytes: int = 128 * 1024 * 1024,
    min_files: int = 2,
    max_input_bytes: int | None = None,
    dry_run: bool = False,
) -> CompactionReport:
    _validate_options(dataset, target_bytes, min_files, max_input_bytes)
    lock_path = dataset.parent / f".{dataset.name}.onager.lock"
    with FileLock(lock_path):
        return _compact_dataset_locked(
            dataset,
            target_bytes=target_bytes,
            min_files=min_files,
            max_input_bytes=max_input_bytes,
            dry_run=dry_run,
        )


def _compact_dataset_locked(
    dataset: Path,
    *,
    target_bytes: int,
    min_files: int,
    max_input_bytes: int | None,
    dry_run: bool,
) -> CompactionReport:
    """Compact selected files into one Parquet part and replace the dataset safely.

    Only local directories are accepted. The existing dataset is moved to a
    sibling backup before the new directory is installed; a failed install
    restores the original directory. Backups are removed only after success.
    """
    plan = plan_compaction(
        dataset,
        target_bytes=target_bytes,
        min_files=min_files,
        max_input_bytes=max_input_bytes,
    )
    input_bytes = plan.input_bytes
    if not plan.selected or dry_run:
        report = CompactionReport(
            dataset=dataset,
            dry_run=dry_run,
            selected_files=len(plan.candidates),
            preserved_files=len(plan.preserved),
            input_bytes=input_bytes,
            output_bytes=0,
            compacted=False,
        )
        _record_compaction(report, plan)
        return report

    parent = dataset.parent
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{dataset.name}.onager-", dir=parent))
    backup = parent / f".{dataset.name}.onager-backup"
    compact_name = f"part-compact-{_plan_hash(plan):s}.parquet"
    try:
        for path in plan.preserved:
            relative = path.relative_to(dataset)
            target = temp_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        input_sql = ", ".join(_quote_literal(str(path)) for path in plan.candidates)
        output = temp_dir / compact_name
        con = duckdb.connect()
        try:
            con.execute(
                f"COPY (SELECT * FROM read_parquet([{input_sql}], union_by_name=true)) "
                f"TO {_quote_literal(str(output))} (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        except duckdb.Error as e:
            raise FatalError(f"compaction failed for {dataset}: {e}") from e
        finally:
            con.close()

        if backup.exists():
            raise FatalError(f"stale Onager backup exists; remove it manually: {backup}")
        os.replace(dataset, backup)
        try:
            os.replace(temp_dir, dataset)
        except OSError:
            os.replace(backup, dataset)
            raise
        shutil.rmtree(backup)
        output_bytes = sum(path.stat().st_size for path in dataset.rglob("*.parquet"))
        report = CompactionReport(
            dataset=dataset,
            dry_run=False,
            selected_files=len(plan.candidates),
            preserved_files=len(plan.preserved),
            input_bytes=input_bytes,
            output_bytes=output_bytes,
            compacted=True,
        )
        _record_compaction(report, plan)
        return report
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def _validate_options(
    dataset: Path,
    target_bytes: int,
    min_files: int,
    max_input_bytes: int | None,
) -> None:
    first_part = dataset.parts[0].lower() if dataset.parts else ""
    if str(dataset).lower().startswith(("s3://", "gs://", "gcs://")) or first_part.rstrip(":") in {
        "s3",
        "gs",
        "gcs",
    }:
        raise FatalError("Onager compaction currently supports local Parquet directories only")
    if not dataset.exists() or not dataset.is_dir():
        raise FatalError(f"dataset directory not found: {dataset}")
    if target_bytes <= 0:
        raise FatalError("target_bytes must be positive")
    if min_files < 2:
        raise FatalError("min_files must be at least 2")
    if max_input_bytes is not None and max_input_bytes <= 0:
        raise FatalError("max_input_bytes must be positive")


def _plan_hash(plan: CompactionPlan) -> str:
    payload = "|".join(f"{path}:{path.stat().st_size}" for path in plan.candidates)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _record_compaction(report: CompactionReport, plan: CompactionPlan) -> None:
    log_dir = report.dataset.parent / ".onager"
    log_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "dataset": str(report.dataset),
        "selected_files": report.selected_files,
        "preserved_files": report.preserved_files,
        "input_bytes": report.input_bytes,
        "output_bytes": report.output_bytes,
        "compacted": report.compacted,
        "dry_run": report.dry_run,
        "target_bytes": plan.target_bytes,
        "max_input_bytes": plan.max_input_bytes,
    }
    with (log_dir / "compaction-log.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
