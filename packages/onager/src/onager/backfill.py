"""Isolated Parquet backfill workspaces with explicit promotion."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scutum import FileLock

from arsenal_core.errors import FatalError


@dataclass(frozen=True)
class BackfillWorkspace:
    run_id: str
    root: Path
    input: Path
    output: Path
    status: str


def create_backfill_workspace(
    dataset: Path,
    run_id: str,
    *,
    root: Path | None = None,
) -> BackfillWorkspace:
    """Snapshot ``dataset`` into an isolated, not-yet-active workspace."""
    _validate_dataset(dataset)
    _validate_run_id(run_id)
    workspace_root = root or dataset.parent / ".onager" / "backfills"
    workspace = workspace_root / run_id
    if workspace.exists():
        raise FatalError(f"backfill workspace already exists: {workspace}")
    input_dir = workspace / "input"
    output_dir = workspace / "output"
    workspace.mkdir(parents=True)
    shutil.copytree(dataset, input_dir)
    output_dir.mkdir()
    _write_status(workspace, "created")
    return BackfillWorkspace(run_id, workspace, input_dir, output_dir, "created")


def run_backfill(
    dataset: Path,
    run_id: str,
    transform: Callable[[Path, Path], None],
    *,
    root: Path | None = None,
) -> BackfillWorkspace:
    """Run a transform against the snapshot and retain success/failure evidence."""
    workspace = create_backfill_workspace(dataset, run_id, root=root)
    _write_status(workspace.root, "running")
    try:
        transform(workspace.input, workspace.output)
    except Exception:
        _write_status(workspace.root, "failed")
        raise
    _write_status(workspace.root, "ready")
    return BackfillWorkspace(
        workspace.run_id, workspace.root, workspace.input, workspace.output, "ready"
    )


def promote_backfill(workspace: BackfillWorkspace, dataset: Path) -> None:
    """Atomically promote a ready backfill output after an explicit operator action."""
    if workspace.status != "ready" or not workspace.output.is_dir():
        raise FatalError("only a ready backfill workspace can be promoted")
    _validate_dataset(dataset)
    lock_path = dataset.parent / f".{dataset.name}.onager.lock"
    backup = dataset.parent / f".{dataset.name}.backfill-backup"
    if backup.exists():
        raise FatalError(f"stale backfill backup exists; remove it manually: {backup}")
    with FileLock(lock_path):
        os.replace(dataset, backup)
        try:
            os.replace(workspace.output, dataset)
        except OSError:
            os.replace(backup, dataset)
            raise
        shutil.rmtree(backup)
    _write_status(workspace.root, "promoted")


def _validate_dataset(dataset: Path) -> None:
    first = dataset.parts[0].lower() if dataset.parts else ""
    if str(dataset).lower().startswith(("s3://", "gs://", "gcs://")) or first.rstrip(":") in {
        "s3",
        "gs",
        "gcs",
    }:
        raise FatalError("Onager backfill currently supports local datasets only")
    if not dataset.is_dir():
        raise FatalError(f"dataset directory not found: {dataset}")


def _validate_run_id(run_id: str) -> None:
    if not run_id or run_id in {".", ".."} or "/" in run_id or "\\" in run_id:
        raise FatalError("backfill run_id must be a non-empty path-safe name")


def _write_status(root: Path, status: str) -> None:
    (root / "status.json").write_text(
        json.dumps({"status": status}, sort_keys=True) + "\n", encoding="utf-8"
    )
