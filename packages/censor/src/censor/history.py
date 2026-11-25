"""Run history under <base>/.arsenal/censor/. Parquet so `gladius query` can aggregate it."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import ValidationError

from arsenal_core.errors import FatalError
from censor.model import AuditReport, Status

CENSOR_SUBDIR: Final = Path(".arsenal") / "censor"


@dataclass(frozen=True)
class CheckChange:
    id: str
    before: Status | None
    after: Status | None


def censor_dir(base: Path) -> Path:
    return base / CENSOR_SUBDIR


def _atomic_write(path: Path, write: Callable[[Path], object]) -> None:
    """Write via a temp file in the same directory, then replace; clean up on failure."""
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _write_parquet(report: AuditReport, tmp: Path) -> None:
    rows = [{"run_id": report.run_id, **c.model_dump(mode="json")} for c in report.checks]
    try:
        pq.write_table(  # pyright: ignore[reportUnknownMemberType]
            pa.Table.from_pylist(rows), tmp, compression="zstd"
        )
    except pa.ArrowException as e:
        raise OSError(f"could not write parquet: {e}") from e


def save_report(report: AuditReport, base: Path, *, markdown: str) -> Path:
    """Write parquet and markdown first and the JSON last: the JSON is what marks a run."""
    root = censor_dir(base)
    runs = root / "runs"
    reports = root / "reports"
    runs.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    gitignore = root / ".gitignore"
    if not gitignore.exists() and not gitignore.is_symlink():
        gitignore.write_text("*\n", encoding="utf-8")
    json_path = runs / f"{report.run_id}.json"
    _atomic_write(runs / f"{report.run_id}.parquet", lambda t: _write_parquet(report, t))
    _atomic_write(
        reports / f"{report.run_id}.md", lambda t: t.write_text(markdown, encoding="utf-8")
    )
    body = report.model_dump_json(indent=2) + "\n"
    _atomic_write(json_path, lambda t: t.write_text(body, encoding="utf-8"))
    return json_path


def _read_report(path: Path) -> AuditReport:
    try:
        return AuditReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValidationError) as e:
        reason = str(e).splitlines()[0] if str(e) else type(e).__name__
        raise FatalError(f"saved run {path.stem} is unreadable ({path}): {reason}") from e


def load_report(base: Path, run_id: str) -> AuditReport:
    if not run_id or run_id.startswith(".") or "/" in run_id or "\\" in run_id:
        raise FatalError(f"invalid run id: {run_id!r}")
    path = censor_dir(base) / "runs" / f"{run_id}.json"
    if not path.is_file():
        raise FatalError(f"no saved run {run_id} in {censor_dir(base)}")
    return _read_report(path)


@dataclass(frozen=True)
class HistoryListing:
    reports: list[AuditReport]
    skipped: list[Path]


def list_reports(base: Path) -> HistoryListing:
    runs = censor_dir(base) / "runs"
    reports: list[AuditReport] = []
    skipped: list[Path] = []
    for path in sorted(runs.glob("*.json")) if runs.is_dir() else []:
        try:
            reports.append(_read_report(path))
        except FatalError:
            skipped.append(path)
    return HistoryListing(reports, skipped)


def diff_reports(before: AuditReport, after: AuditReport) -> list[CheckChange]:
    a = {c.id: c.status for c in before.checks}
    b = {c.id: c.status for c in after.checks}
    return [
        CheckChange(check_id, a.get(check_id), b.get(check_id))
        for check_id in sorted(a.keys() | b.keys())
        if a.get(check_id) != b.get(check_id)
    ]
