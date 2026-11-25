"""Measure, guard and profile every data target of an audit, before the checks run."""

from __future__ import annotations

import duckdb

from arsenal_core.errors import FatalError
from censor.data import QueryTimeout, TargetUnreadable, open_source, profile
from censor.derive import derive_targets
from censor.history import scratch_dir
from censor.limits import Scale, describe, exceeds, guidance, list_files, measure_files
from censor.postgres import precheck_postgres, redact
from censor.reading import DataReading
from censor.target import AuditContext, DataTarget, FilesTarget, PostgresTarget


def _first_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[0] if lines else "the query failed without a message"


def _skipped_for_scale(
    ctx: AuditContext, target: DataTarget, label: str, scale: Scale
) -> DataReading:
    notice = guidance(
        label,
        scale,
        is_files=isinstance(target, FilesTarget),
        since_example=f"{ctx.now:%Y-%m}-01",
    )
    reason = f"skipped, over the scale limit: {describe(scale)}"
    return DataReading(label, skipped=reason, notice=notice)


def precheck(target: DataTarget) -> None:
    """Refuse a target that is not there before anything is written or connected."""
    if isinstance(target, FilesTarget):
        if not list_files(target.pattern):
            raise FatalError(f"no file matches {target.pattern}")
    elif isinstance(target, PostgresTarget):
        precheck_postgres(target)
    elif not target.path.is_file():
        raise FatalError(f"no DuckDB file at {target.path}")


def read_target(
    ctx: AuditContext,
    target: DataTarget,
    label: str,
    key: tuple[str, ...],
    time_column: str | None,
    *,
    strict: bool,
) -> DataReading:
    """Profile one target. `strict` is for a target the user named: it must be readable."""

    def clean(error: Exception) -> str:
        text = str(error)
        return redact(text, target) if isinstance(target, PostgresTarget) else text

    try:
        precheck(target)
        if isinstance(target, FilesTarget):  # sized from `stat` before any file is opened
            scale = measure_files(list_files(target.pattern))
            if exceeds(scale) and not ctx.scan.narrowed:
                return _skipped_for_scale(ctx, target, label, scale)
        with (
            scratch_dir(ctx.root) as scratch,
            open_source(target, ctx.scan, key, time_column, scratch) as source,
        ):
            if exceeds(source.scale) and not ctx.scan.narrowed:
                return _skipped_for_scale(ctx, target, label, source.scale)
            return DataReading(
                label,
                source.key,
                source.time_column,
                profile=profile(source),
                sample=source.sample,
                newest_file_at=source.newest_file_at,
            )
    except QueryTimeout as e:
        return DataReading(label, key, time_column, skipped=str(e))
    except TargetUnreadable as e:
        return DataReading(label, skipped=clean(e), tool_error=clean(e) if strict else None)
    except FatalError as e:
        if strict:
            raise
        return DataReading(label, skipped=clean(e))
    except duckdb.Error as e:
        return DataReading(label, key, time_column, failure=_first_line(clean(e)))


def read_targets(ctx: AuditContext) -> tuple[DataReading, ...]:
    if ctx.data is not None:
        reading = read_target(ctx, ctx.data, ctx.data.label, ctx.key, ctx.time_column, strict=True)
        return (reading,)
    if ctx.project is None:
        return ()
    readings: list[DataReading] = []
    for derived in derive_targets(ctx.project):
        if derived.target is None:
            readings.append(DataReading(derived.label, skipped=derived.reason))
        else:
            readings.append(
                read_target(ctx, derived.target, derived.label, derived.key, None, strict=False)
            )
    return tuple(readings)
