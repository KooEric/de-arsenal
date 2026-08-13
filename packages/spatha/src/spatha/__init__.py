"""Spatha — deterministic task DAG planning and execution."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("spatha")
except PackageNotFoundError:
    __version__ = "0.3.0"

from spatha.workflow import ExecutionWindow, TaskSpec, Workflow

__all__ = ["ExecutionWindow", "TaskSpec", "Workflow"]
