from datetime import time
from pathlib import Path

import pytest
from pydantic import ValidationError
from spatha import ExecutionWindow, TaskSpec, Workflow


def test_plan_is_topological_and_priority_stable() -> None:
    workflow = Workflow(
        tasks=[
            TaskSpec(name="load"),
            TaskSpec(name="quality", depends_on=["load"], priority=1),
            TaskSpec(name="lineage", depends_on=["load"], priority=2),
        ]
    )

    assert [task.name for task in workflow.plan()] == ["load", "lineage", "quality"]


def test_run_calls_tasks_in_plan_order() -> None:
    workflow = Workflow(
        tasks=[TaskSpec(name="a"), TaskSpec(name="b", depends_on=["a"])]
    )
    seen: list[str] = []

    assert workflow.run(lambda task: seen.append(task.name)) == ["a", "b"]
    assert seen == ["a", "b"]


def test_unknown_dependency_duplicate_and_cycle_are_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown dependencies"):
        Workflow(tasks=[TaskSpec(name="a", depends_on=["missing"])])
    with pytest.raises(ValidationError, match="unique"):
        Workflow(tasks=[TaskSpec(name="a"), TaskSpec(name="a")])
    cycle = Workflow.model_construct(
        tasks=[TaskSpec(name="a", depends_on=["b"]), TaskSpec(name="b", depends_on=["a"])]
    )
    with pytest.raises(ValueError, match="cycle"):
        cycle.plan()


def test_ready_tasks_require_signals_and_execution_window() -> None:
    workflow = Workflow(
        tasks=[
            TaskSpec(name="load", signals=["raw_ready"]),
            TaskSpec(
                name="publish",
                depends_on=["load"],
                window=ExecutionWindow(start=time(9), end=time(17)),
            ),
        ]
    )
    assert workflow.ready_tasks(signals=set(), now=time(10)) == []
    assert [task.name for task in workflow.ready_tasks(signals={"raw_ready"}, now=time(10))] == [
        "load"
    ]
    assert workflow.ready_tasks(completed={"load"}, now=time(18)) == []


def test_run_ready_releases_dependencies_and_uses_task_locks(tmp_path: Path) -> None:
    workflow = Workflow(
        tasks=[
            TaskSpec(name="load", signals=["raw_ready"]),
            TaskSpec(name="publish", depends_on=["load"]),
        ]
    )
    seen: list[str] = []

    result = workflow.run_ready(
        lambda task: seen.append(task.name),
        signals={"raw_ready"},
        lock_dir=tmp_path,
    )

    assert result == ["load", "publish"]
    assert seen == result
    assert list(tmp_path.glob("*.lock")) == []
