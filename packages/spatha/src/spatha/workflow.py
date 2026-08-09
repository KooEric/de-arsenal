"""Signal-aware dependency DAG validation, planning, and execution."""

import time
from collections.abc import Callable
from datetime import datetime
from datetime import time as wall_time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from scutum import file_lock


class ExecutionWindow(BaseModel):
    """Inclusive start/exclusive end wall-clock window in UTC/local caller time."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: wall_time
    end: wall_time

    @model_validator(mode="after")
    def _must_be_forward_window(self) -> "ExecutionWindow":
        if self.start >= self.end:
            raise ValueError("execution window start must be before end")
        return self

    def contains(self, current: wall_time) -> bool:
        return self.start <= current < self.end


class TaskSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    depends_on: list[str] = Field(default_factory=list)
    priority: int = 0
    signals: list[str] = Field(default_factory=list)
    window: ExecutionWindow | None = None

    @field_validator("name")
    @classmethod
    def _name_is_path_safe(cls, value: str) -> str:
        if not value or "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("task name must be non-empty and path-safe")
        return value


class Workflow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tasks: list[TaskSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_graph(self) -> "Workflow":
        names = [task.name for task in self.tasks]
        if len(set(names)) != len(names):
            raise ValueError("workflow task names must be unique")
        known = set(names)
        missing = sorted(
            {dep for task in self.tasks for dep in task.depends_on if dep not in known}
        )
        if missing:
            raise ValueError(f"workflow has unknown dependencies: {', '.join(missing)}")
        return self

    def plan(self) -> list[TaskSpec]:
        """Return a stable topological order, preferring higher priority tasks."""
        by_name = {task.name: task for task in self.tasks}
        remaining = {task.name: set(task.depends_on) for task in self.tasks}
        ordered: list[TaskSpec] = []
        while remaining:
            ready = [name for name, deps in remaining.items() if not deps]
            if not ready:
                raise ValueError("workflow contains a dependency cycle")
            ready.sort(key=lambda name: (-by_name[name].priority, name))
            for name in ready:
                ordered.append(by_name[name])
                del remaining[name]
            for deps in remaining.values():
                deps.difference_update(ready)
        return ordered

    def ready_tasks(
        self,
        *,
        completed: set[str] | frozenset[str] = frozenset(),
        signals: set[str] | frozenset[str] = frozenset(),
        now: wall_time | None = None,
    ) -> list[TaskSpec]:
        """Return runnable tasks, sorted by priority then name.

        A task is runnable only when all dependencies and declared preparation
        signals exist and its optional execution window contains ``now``.
        """
        current = now or datetime.now().time()
        ready = [
            task
            for task in self.tasks
            if task.name not in completed
            and set(task.depends_on).issubset(completed)
            and set(task.signals).issubset(signals)
            and (task.window is None or task.window.contains(current))
        ]
        return sorted(ready, key=lambda task: (-task.priority, task.name))

    def run(self, runner: Callable[[TaskSpec], None]) -> list[str]:
        """Run tasks in plan order and return completed task names."""
        completed: list[str] = []
        for task in self.plan():
            runner(task)
            completed.append(task.name)
        return completed

    def run_ready(
        self,
        runner: Callable[[TaskSpec], None],
        *,
        completed: set[str] | None = None,
        signals: set[str] | frozenset[str] = frozenset(),
        now: wall_time | None = None,
        lock_dir: Path | None = None,
        lock_attempts: int = 5,
        backoff_seconds: float = 0.1,
        sleep: Callable[[float], None] = time.sleep,
    ) -> list[str]:
        """Run all currently runnable tasks until dependencies or signals block.

        ``lock_dir`` enables cross-process task locks. The returned list only
        contains tasks completed by this invocation, allowing an external
        scheduler to persist the frontier and call this method again later.
        """
        completed_names = set(completed or ())
        executed: list[str] = []
        while True:
            ready = self.ready_tasks(completed=completed_names, signals=signals, now=now)
            if not ready:
                return executed
            for task in ready:
                if lock_dir is None:
                    runner(task)
                else:
                    with file_lock(
                        lock_dir / f"{task.name}.lock",
                        attempts=lock_attempts,
                        backoff_seconds=backoff_seconds,
                        sleep=sleep,
                    ):
                        runner(task)
                completed_names.add(task.name)
                executed.append(task.name)
