"""Built-in task actions: an argv command, or waiting on a Ballista run.

``Workflow.run``/``run_ready`` take any callable; ``default_runner`` executes
the action a task declares. Commands run without a shell. The Ballista action
shells out to ``ballista reconcile`` so Spatha does not depend on Ballista.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any, cast

from spatha.workflow import BallistaReconcileAction, TaskSpec

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]

_SUCCEEDED = "succeeded"
_TERMINAL_FAILURES = frozenset({"failed", "cancelled", "lost"})


class TaskFailedError(RuntimeError):
    """A task's action finished unsuccessfully; dependents must not run."""


class TaskTimeoutError(TaskFailedError):
    """A waiting action ran out of time; re-running the workflow resumes waiting."""


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), check=False, capture_output=True, text=True)


def run_command(task: TaskSpec, *, run: CommandRunner = _run) -> None:
    if task.command is None:
        raise ValueError(f"task {task.name!r} has no command")
    completed = run(task.command)
    if completed.returncode != 0:
        # Output is not echoed: commands may print connection strings.
        raise TaskFailedError(f"task {task.name!r} exited with code {completed.returncode}")


def wait_for_ballista_run(
    task: TaskSpec,
    *,
    run: CommandRunner = _run,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Reconcile until the run succeeds (return its record) or fails (raise)."""

    action = task.ballista_reconcile
    if action is None:
        raise ValueError(f"task {task.name!r} has no ballista_reconcile action")
    deadline = clock() + action.timeout_seconds
    while True:
        record = _reconcile(action, run)
        status = record.get("status")
        if status == _SUCCEEDED:
            return record
        if status in _TERMINAL_FAILURES:
            raise TaskFailedError(f"Ballista run {action.run_id!r} ended {status}")
        if clock() + action.poll_seconds > deadline:
            raise TaskTimeoutError(
                f"Ballista run {action.run_id!r} still {status} after "
                f"{action.timeout_seconds:.0f}s; re-run the workflow to keep waiting"
            )
        sleep(action.poll_seconds)


def _reconcile(action: BallistaReconcileAction, run: CommandRunner) -> dict[str, Any]:
    completed = run(
        [
            sys.executable,
            "-m",
            "ballista.cli",
            "reconcile",
            action.run_id,
            "--profile",
            str(action.profile),
            "--state-dir",
            str(action.state_dir),
        ]
    )
    if completed.returncode != 0:
        raise TaskFailedError(
            f"ballista reconcile for {action.run_id!r} exited with code {completed.returncode}"
        )
    try:
        record: Any = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise TaskFailedError("ballista reconcile did not print a JSON run record") from exc
    if not isinstance(record, dict):
        raise TaskFailedError("ballista reconcile did not print a JSON run record")
    return cast(dict[str, Any], record)


def default_runner(task: TaskSpec) -> None:
    """Execute the built-in action a task declares."""

    if task.command is not None:
        run_command(task)
    elif task.ballista_reconcile is not None:
        wait_for_ballista_run(task)
    else:
        raise ValueError(f"task {task.name!r} declares no action; pass your own runner")


__all__ = [
    "TaskFailedError",
    "TaskTimeoutError",
    "default_runner",
    "run_command",
    "wait_for_ballista_run",
]
