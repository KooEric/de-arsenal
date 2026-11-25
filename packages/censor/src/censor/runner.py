"""Run check groups; a crashing group becomes one `error` check instead of aborting."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from censor.model import CheckResult, Dimension, Status
from censor.result import make_result
from censor.target import ALL_KINDS, TargetKind


class Auditable(Protocol):
    @property
    def kind(self) -> TargetKind: ...
    @property
    def root(self) -> Path: ...


C = TypeVar("C", bound=Auditable)


@dataclass(frozen=True)
class CheckGroup(Generic[C]):
    name: str
    dimension: Dimension
    fn: Callable[[C], list[CheckResult]]
    kinds: frozenset[TargetKind] = ALL_KINDS  # target kinds the group applies to


def run_checks(ctx: C, groups: Sequence[CheckGroup[C]]) -> list[CheckResult]:
    """A group that does not apply to `ctx.kind` is skipped, which leaves its dimension `na`."""
    results: list[CheckResult] = []
    for group in groups:
        if ctx.kind not in group.kinds:
            continue
        try:
            results.extend(group.fn(ctx))
        except Exception as error:  # recorded as an `error` check, never swallowed
            results.append(
                make_result(
                    f"{group.name}.crashed",
                    group.dimension,
                    1,
                    Status.ERROR,
                    str(ctx.root),
                    f"{type(error).__name__}: {error}",
                    "report this as a Censor bug with the evidence above",
                )
            )
    return results
