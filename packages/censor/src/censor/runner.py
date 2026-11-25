"""Run check groups; a crashing group becomes one `error` check instead of aborting."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from censor.context import ProjectContext
from censor.model import CheckResult, Dimension, Status

CheckFn = Callable[[ProjectContext], list[CheckResult]]


@dataclass(frozen=True)
class CheckGroup:
    name: str
    dimension: Dimension
    fn: CheckFn


def run_checks(ctx: ProjectContext, groups: Sequence[CheckGroup]) -> list[CheckResult]:
    results: list[CheckResult] = []
    for group in groups:
        try:
            results.extend(group.fn(ctx))
        except Exception as error:  # recorded as an `error` check, never swallowed
            results.append(
                CheckResult(
                    id=f"{group.name}.crashed",
                    dimension=group.dimension,
                    weight=1,
                    status=Status.ERROR,
                    subject=str(ctx.root),
                    evidence=f"{type(error).__name__}: {error}",
                    fix="report this as a Censor bug with the evidence above",
                )
            )
    return results
