"""Onager — safe small-file compaction for local Parquet datasets."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("onager")
except PackageNotFoundError:
    __version__ = "0.2.2"

from onager.backfill import (
    BackfillWorkspace,
    create_backfill_workspace,
    promote_backfill,
    run_backfill,
)
from onager.compact import CompactionReport, compact_dataset, plan_compaction

__all__ = [
    "BackfillWorkspace",
    "CompactionReport",
    "compact_dataset",
    "create_backfill_workspace",
    "plan_compaction",
    "promote_backfill",
    "run_backfill",
]
