"""Spatha — deterministic task DAG planning and execution."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("spatha")
except PackageNotFoundError:
    __version__ = "0.6.16"

from spatha.actions import TaskFailedError, TaskTimeoutError, default_runner
from spatha.workflow import BallistaReconcileAction, ExecutionWindow, TaskSpec, Workflow

__all__ = [
    "BallistaReconcileAction",
    "ExecutionWindow",
    "TaskFailedError",
    "TaskSpec",
    "TaskTimeoutError",
    "Workflow",
    "default_runner",
]
