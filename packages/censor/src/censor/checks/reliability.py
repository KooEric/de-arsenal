"""Did the last runs finish cleanly, and is the output still there when they did not?"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final, cast

from arsenal_core.errors import FatalError
from censor.context import SpecFile
from censor.model import CheckResult, Dimension, Status
from censor.result import make_result
from censor.state import PipelineState, read_dataset_state, read_pipeline_state
from censor.target import AuditContext

DLQ_FAIL_RATIO: Final = 0.05  # more than 5% of the rows set aside is a failure, less a warning
_DIM: Final = Dimension.RELIABILITY
_ALERT_STATES: Final = ("failed", "quarantined")  # same as `arsenal status`
_NEVER_RUN: Final = "never run: nothing to measure"


def _r(
    kind: str, spec: SpecFile, weight: int, status: Status, evidence: str, fix: str = ""
) -> CheckResult:
    return make_result(
        f"reliability.{kind}:{spec.name}", _DIM, weight, status, spec.name, evidence, fix
    )


def _not_measured(spec: SpecFile, reason: str) -> list[CheckResult]:
    return [
        _r("failed_units", spec, 3, Status.NA, reason),
        _r("rejected_rows", spec, 2, Status.NA, reason),
        _r("output_kept", spec, 2, Status.NA, reason),
    ]


def _failed_units(spec: SpecFile, state: PipelineState) -> CheckResult:
    counts = {s: state.units.get(s, 0) for s in _ALERT_STATES}
    total = sum(state.units.values())
    if not any(counts.values()):
        return _r("failed_units", spec, 3, Status.PASS, f"{total} units, none failed")
    found = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
    return _r(
        "failed_units",
        spec,
        3,
        Status.FAIL,
        f"{found} of {total} units",
        "see `arsenal status`; rerun with `arsenal run`, or review `arsenal collect dlq list`",
    )


def _rejected_rows(spec: SpecFile, state: PipelineState) -> CheckResult:
    rejected = state.rejected_rows
    if rejected == 0:
        return _r("rejected_rows", spec, 2, Status.PASS, "no row was set aside by validation")
    ratio = rejected / (state.rows_done + rejected)
    status = Status.FAIL if ratio > DLQ_FAIL_RATIO else Status.WARN
    return _r(
        "rejected_rows",
        spec,
        2,
        status,
        f"{rejected} rejected rows in the DLQ, {ratio:.1%} of {state.rows_done + rejected} rows",
        "review the DLQ; fix the source or the rule, then retry the unit",
    )


def _local_output(root: Path, spec: SpecFile) -> Path | None:
    raw = spec.raw or {}
    sink_raw: object = raw.get("sink")
    if not isinstance(sink_raw, dict):
        return None
    sink = cast(dict[str, Any], sink_raw)
    path: object = sink.get("path")
    if sink.get("type") not in ("parquet", "duckdb") or not isinstance(path, str) or "://" in path:
        return None
    return Path(path) if Path(path).is_absolute() else root / path


def _has_output(path: Path) -> bool:
    if path.is_file():
        return path.stat().st_size > 0
    return path.is_dir() and any(p.is_file() for p in path.rglob("*"))


def _output_kept(root: Path, spec: SpecFile, state: PipelineState) -> CheckResult:
    if not any(state.units.get(s, 0) for s in _ALERT_STATES):
        return _r("output_kept", spec, 2, Status.NA, "no failed unit")
    output = _local_output(root, spec)
    if output is None:
        return _r("output_kept", spec, 2, Status.NA, "the sink is not a local path")
    if _has_output(output):
        return _r(
            "output_kept", spec, 2, Status.PASS, "units failed and the earlier output remains"
        )
    return _r(
        "output_kept",
        spec,
        2,
        Status.FAIL,
        "units failed and the sink has no output: readers have nothing to fall back on",
        "rerun the pipeline; keep the previous output until a run succeeds",
    )


def _pipeline(ctx: AuditContext, spec: SpecFile) -> list[CheckResult]:
    try:
        state = read_pipeline_state(ctx.root, spec, ctx.now)
    except FatalError as e:
        return [
            make_result(
                f"reliability.state:{spec.name}",
                _DIM,
                1,
                Status.ERROR,
                spec.name,
                str(e),
                "repair or remove the unreadable record, then audit again",
            )
        ]
    if state is None:
        return _not_measured(spec, _NEVER_RUN)
    if state.busy:
        return [
            *_not_measured(spec, "state could not be read"),
            _r(
                "active_run",
                spec,
                1,
                Status.WARN,
                "the state database could not be read: a run is probably running and writing it",
                "audit again when the run has finished",
            ),
        ]
    results = [
        _failed_units(spec, state),
        _rejected_rows(spec, state),
        _output_kept(ctx.root, spec, state),
    ]
    if state.lease_until is not None:
        results.append(
            _r(
                "active_run",
                spec,
                1,
                Status.WARN,
                f"a run is running (lease until {state.lease_until:%Y-%m-%d %H:%M:%S} UTC): "
                "the state is still changing",
                "audit again when the run has finished",
            )
        )
    return results


def _dataset_publication(ctx: AuditContext) -> list[CheckResult]:
    dataset = read_dataset_state(ctx.root, ctx.now)
    if dataset is None:
        return []
    check_id, subject = "reliability.dataset_publication", ".arsenal/dataset.duckdb"
    if dataset.state != "failed":
        evidence = "the last `arsenal dataset` run did not fail"
        return [make_result(check_id, _DIM, 2, Status.NA, subject, evidence)]
    if dataset.has_published:
        evidence = "the last run failed and the previous publication is still served"
        return [make_result(check_id, _DIM, 2, Status.PASS, subject, evidence)]
    return [
        make_result(
            check_id,
            _DIM,
            2,
            Status.FAIL,
            subject,
            "the last run failed and there is no published table to fall back on",
            "check the input, then `arsenal dataset preview` and `arsenal dataset apply`",
        )
    ]


def check_reliability(ctx: AuditContext) -> list[CheckResult]:
    if ctx.project is None:
        return []
    results: list[CheckResult] = []
    for spec in ctx.project.pipelines:
        results.extend(_pipeline(ctx, spec))
    results.extend(_dataset_publication(ctx))
    return results
