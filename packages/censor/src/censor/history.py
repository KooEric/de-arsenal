"""Run history under <base>/.arsenal/censor/. Parquet so `gladius query` can aggregate it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pyarrow as pa
import pyarrow.parquet as pq

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


def save_report(report: AuditReport, base: Path, *, markdown: str) -> Path:
    root = censor_dir(base)
    runs = root / "runs"
    reports = root / "reports"
    runs.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    gitignore = root / ".gitignore"
    if not gitignore.exists() and not gitignore.is_symlink():
        gitignore.write_text("*\n", encoding="utf-8")
    json_path = runs / f"{report.run_id}.json"
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    rows = [{"run_id": report.run_id, **c.model_dump(mode="json")} for c in report.checks]
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        pa.Table.from_pylist(rows), runs / f"{report.run_id}.parquet", compression="zstd"
    )
    (reports / f"{report.run_id}.md").write_text(markdown, encoding="utf-8")
    return json_path


def load_report(base: Path, run_id: str) -> AuditReport:
    path = censor_dir(base) / "runs" / f"{run_id}.json"
    if not path.is_file():
        raise FatalError(f"no saved run {run_id} in {censor_dir(base)}")
    return AuditReport.model_validate_json(path.read_text(encoding="utf-8"))


def list_reports(base: Path) -> list[AuditReport]:
    runs = censor_dir(base) / "runs"
    paths = sorted(runs.glob("*.json")) if runs.is_dir() else []
    return [AuditReport.model_validate_json(p.read_text(encoding="utf-8")) for p in paths]


def diff_reports(before: AuditReport, after: AuditReport) -> list[CheckChange]:
    a = {c.id: c.status for c in before.checks}
    b = {c.id: c.status for c in after.checks}
    return [
        CheckChange(check_id, a.get(check_id), b.get(check_id))
        for check_id in sorted(a.keys() | b.keys())
        if a.get(check_id) != b.get(check_id)
    ]
