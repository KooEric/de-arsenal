"""How recent is the last success, and is there a limit that says when it is too old?"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from arsenal_core.errors import FatalError
from censor.context import SpecFile
from censor.model import CheckResult, Dimension, Status
from censor.reading import DataReading
from censor.result import make_result
from censor.state import DatasetState, read_dataset_state, read_pipeline_state
from censor.target import AuditContext

_DIM: Final = Dimension.FRESHNESS
_DATASET: Final = ".arsenal/dataset.duckdb"


def age_hours(now: datetime, then: datetime) -> float:
    return (now - then) / timedelta(hours=1)


def _last_success(ctx: AuditContext, spec: SpecFile) -> CheckResult:
    check_id = f"freshness.last_success:{spec.name}"

    def result(status: Status, evidence: str, fix: str = "") -> CheckResult:
        return make_result(check_id, _DIM, 2, status, spec.name, evidence, fix)

    try:
        state = read_pipeline_state(ctx.root, spec, ctx.now)
    except FatalError:  # an unreadable DLQ record; reliability reports it as an error
        return result(Status.NA, "state could not be read")
    if state is None:
        return result(Status.NA, "never run: nothing to measure")
    if state.busy:
        return result(Status.NA, "state could not be read")
    if state.last_completed_at is None:
        return result(Status.FAIL, "no unit has ever completed", "fix the failures, then rerun")
    age = age_hours(ctx.now, state.last_completed_at)
    seen = f"last unit completed {state.last_completed_at:%Y-%m-%d %H:%M} UTC ({age:.1f}h ago)"
    limit = ctx.max_age_hours
    if limit is not None and age > limit:
        return result(
            Status.FAIL, f"{seen}, limit {limit:g}h", "run the pipeline; check its schedule"
        )
    return result(Status.PASS, seen)


def _dataset_state(dataset: DatasetState) -> CheckResult:
    check_id = "freshness.dataset_state"
    fix = "check the input, then `arsenal dataset preview` and `arsenal dataset apply`"
    if dataset.state == "published":
        when = f"{dataset.last_success:%Y-%m-%d %H:%M} UTC" if dataset.last_success else "unknown"
        return make_result(check_id, _DIM, 3, Status.PASS, _DATASET, f"published, last {when}")
    if dataset.state == "stale":
        evidence = f"stale: the input has not changed for more than {dataset.max_age_hours:g}h"
        return make_result(check_id, _DIM, 3, Status.FAIL, _DATASET, evidence, fix)
    if dataset.state == "failed":
        evidence = "the last `arsenal dataset` run failed"
        return make_result(check_id, _DIM, 3, Status.FAIL, _DATASET, evidence, fix)
    reasons = {
        "busy": "locked by a running `arsenal dataset`",
        "unreadable": "the store could not be read",
    }
    reason = reasons.get(dataset.state, "never run")
    return make_result(check_id, _DIM, 3, Status.NA, _DATASET, reason)


def _max_age_set(ctx: AuditContext, dataset: DatasetState | None) -> CheckResult:
    check_id = "freshness.max_age_set"
    if ctx.max_age_hours is not None:
        evidence = f"--max-age-hours {ctx.max_age_hours:g}"
        return make_result(check_id, _DIM, 1, Status.PASS, "project", evidence)
    if dataset is not None and dataset.max_age_hours is not None:
        evidence = f"arsenal dataset max_age_hours: {dataset.max_age_hours:g}"
        return make_result(check_id, _DIM, 1, Status.PASS, _DATASET, evidence)
    return make_result(
        check_id,
        _DIM,
        1,
        Status.WARN,
        "project",
        "no freshness limit: nothing says when this data is too old",
        "pass `--max-age-hours`, or set it with `arsenal dataset apply --max-age-hours`",
    )


def _project(ctx: AuditContext) -> list[CheckResult]:
    if ctx.project is None:
        return []
    results = [_last_success(ctx, spec) for spec in ctx.project.pipelines]
    dataset = read_dataset_state(ctx.root, ctx.now)
    if dataset is not None:
        results.append(_dataset_state(dataset))
    if any(r.status != Status.NA for r in results):
        results.append(_max_age_set(ctx, dataset))
    return results


def _data_age(ctx: AuditContext, reading: DataReading) -> CheckResult:
    """A bare dataset: the newest time-column value, or the newest file, against the limit."""
    check_id = f"freshness.data_age:{reading.label}"

    def result(status: Status, evidence: str, fix: str = "") -> CheckResult:
        return make_result(check_id, _DIM, 2, status, reading.label, evidence, fix)

    if reading.profile is None:
        return result(Status.NA, reading.skipped or reading.failure or "not profiled")
    if reading.time_column is not None:
        newest, what = reading.profile.time_max_at, f"newest {reading.time_column}"
    else:
        newest, what = reading.newest_file_at, "newest file written"
    if newest is None:
        return result(Status.NA, "no time column given: pass --time-column")
    age = age_hours(ctx.now, newest)
    seen = f"{what} {newest:%Y-%m-%d %H:%M} UTC ({age:.1f}h ago)"
    limit = ctx.max_age_hours
    if limit is None:
        return result(Status.NA, f"{seen}; pass --max-age-hours to judge it")
    if age > limit:
        return result(Status.FAIL, f"{seen}, limit {limit:g}h", "check that the load still runs")
    return result(Status.PASS, f"{seen}, limit {limit:g}h")


def check_freshness(ctx: AuditContext) -> list[CheckResult]:
    if ctx.kind == "project":
        return _project(ctx)
    return [_data_age(ctx, reading) for reading in ctx.readings]
