"""Quick tier: has the pipeline written anything lately? File times only, no data is opened."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from censor.checks.freshness import DEFAULT_MAX_AGE_HOURS, age_hours
from censor.context import SpecFile
from censor.model import CheckResult, Dimension, Status
from censor.result import make_result
from censor.state import pipeline_identity
from censor.target import AuditContext

_FIX: Final = "run the pipeline; check its schedule"


def _newest_mtime(path: Path) -> datetime | None:
    """The newest file time at `path`: the file itself, or any file under the directory."""
    if path.is_file():
        stamps = [path.stat().st_mtime]
    elif path.is_dir():
        stamps = [p.stat().st_mtime for p in path.rglob("*") if p.is_file()]
    else:
        return None
    return datetime.fromtimestamp(max(stamps), UTC) if stamps else None


def _local_sink(root: Path, spec: SpecFile) -> Path | None:
    """The sink's local path, if it has one: not remote, not built from an env variable."""
    sink: object = (spec.raw or {}).get("sink")
    path: object = cast(dict[str, object], sink).get("path") if isinstance(sink, dict) else None
    if not isinstance(path, str) or not path or "://" in path or "$" in path:
        return None
    local = Path(path)
    return local if local.is_absolute() else root / local


def _state_files(root: Path, spec: SpecFile) -> list[Path]:
    identity = pipeline_identity(root, spec)
    if identity is None:
        return []
    name, directory = identity
    db = directory / f"{name}.db"
    return [db, db.with_name(db.name + "-wal")]


def _newest_write(root: Path, spec: SpecFile) -> tuple[datetime, str] | None:
    """Output first: a no-op run still touches the state file, so it says less."""
    sink = _local_sink(root, spec)
    if sink is not None and (when := _newest_mtime(sink)) is not None:
        return when, "newest output written"
    stamps = [t for p in _state_files(root, spec) if (t := _newest_mtime(p)) is not None]
    return (max(stamps), "state last written") if stamps else None


def _staleness(ctx: AuditContext, spec: SpecFile) -> CheckResult:
    check_id = f"operability.freshness:{spec.name}"

    def result(status: Status, evidence: str, fix: str = "") -> CheckResult:
        return make_result(check_id, Dimension.OPERABILITY, 2, status, spec.name, evidence, fix)

    found = _newest_write(ctx.root, spec)
    if found is None:
        return result(Status.NA, "no output or state yet: never run")
    when, what = found
    age = age_hours(ctx.now, when)
    seen = f"{what} {when:%Y-%m-%d %H:%M} UTC ({age / 24:.1f}d ago)"
    if ctx.max_age_hours is not None:
        limit, label = ctx.max_age_hours, f"limit {ctx.max_age_hours:g}h"
    else:
        limit, label = DEFAULT_MAX_AGE_HOURS, f"default limit {DEFAULT_MAX_AGE_HOURS:g}h"
    if age > limit:
        return result(Status.WARN, f"{seen}, {label}", _FIX)
    return result(Status.PASS, f"{seen}, {label}")


def check_staleness(ctx: AuditContext) -> list[CheckResult]:
    if ctx.project is None:
        return []
    return [_staleness(ctx, spec) for spec in ctx.project.pipelines]
