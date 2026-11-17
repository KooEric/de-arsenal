from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import ValidationError
from spatha import TaskFailedError, TaskSpec, TaskTimeoutError, Workflow
from spatha.actions import default_runner, run_command, wait_for_ballista_run


class Script:
    """Replays a sequence of ballista reconcile outputs."""

    def __init__(self, *statuses: str, returncode: int = 0) -> None:
        self.statuses = list(statuses)
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        out = json.dumps({"run_id": "r1", "status": status})
        return subprocess.CompletedProcess(list(argv), self.returncode, out, "")


def reconcile_task(**action: Any) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "name": "wait-daily",
            "ballista_reconcile": {
                "run_id": "r1",
                "profile": "p.yaml",
                "poll_seconds": 5,
                **action,
            },
        }
    )


def test_waits_until_the_run_succeeds() -> None:
    script = Script("submitted", "running", "succeeded")
    sleeps: list[float] = []

    record = wait_for_ballista_run(reconcile_task(), run=script, sleep=sleeps.append)

    assert record["status"] == "succeeded"
    assert sleeps == [5, 5]
    argv = script.calls[0]
    assert argv[1:5] == ["-m", "ballista.cli", "reconcile", "r1"]
    assert argv[argv.index("--state-dir") + 1] == ".arsenal"


@pytest.mark.parametrize("status", ["failed", "cancelled", "lost"])
def test_terminal_failure_fails_the_task(status: str) -> None:
    with pytest.raises(TaskFailedError, match=status):
        wait_for_ballista_run(reconcile_task(), run=Script(status), sleep=lambda _: None)


def test_times_out_without_marking_the_run_failed() -> None:
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(TaskTimeoutError, match="re-run the workflow"):
        wait_for_ballista_run(
            reconcile_task(timeout_seconds=12),
            run=Script("running"),
            sleep=sleep,
            clock=lambda: now[0],
        )


def test_reconcile_command_failure_is_a_task_failure() -> None:
    with pytest.raises(TaskFailedError, match="exited with code 1"):
        wait_for_ballista_run(reconcile_task(), run=Script("running", returncode=1))


def test_non_json_output_is_a_task_failure() -> None:
    def garbage(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(argv), 0, "not json", "")

    with pytest.raises(TaskFailedError, match="JSON"):
        wait_for_ballista_run(reconcile_task(), run=garbage)


def test_command_task_runs_argv_without_shell_and_hides_output() -> None:
    seen: list[list[str]] = []

    def run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        seen.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 3, "", "password=SECRET")

    task = TaskSpec(name="load", command=["pugio", "run", "x.yaml"])
    with pytest.raises(TaskFailedError) as caught:
        run_command(task, run=run)
    assert seen == [["pugio", "run", "x.yaml"]]
    assert "SECRET" not in str(caught.value)


def test_task_cannot_declare_two_actions() -> None:
    with pytest.raises(ValidationError, match="either command or ballista_reconcile"):
        TaskSpec.model_validate(
            {
                "name": "both",
                "command": ["true"],
                "ballista_reconcile": {"run_id": "r", "profile": "p.yaml"},
            }
        )


def test_default_runner_requires_an_action() -> None:
    with pytest.raises(ValueError, match="declares no action"):
        default_runner(TaskSpec(name="bare"))


def test_reconcile_task_gates_dependents_in_a_workflow() -> None:
    order: list[str] = []
    script = Script("running", "succeeded")
    workflow = Workflow(
        tasks=[
            reconcile_task(),
            TaskSpec(name="publish", depends_on=["wait-daily"], command=["echo", "done"]),
        ]
    )

    def runner(task: TaskSpec) -> None:
        if task.ballista_reconcile is not None:
            wait_for_ballista_run(task, run=script, sleep=lambda _seconds: order.append("poll"))
        order.append(task.name)

    assert workflow.run(runner) == ["wait-daily", "publish"]
    assert order == ["poll", "wait-daily", "publish"]
