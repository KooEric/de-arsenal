"""Can someone else run, schedule and trace this project?"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Final

from censor.context import ProjectContext
from censor.model import CheckResult, Dimension, Status

# Fixed locations only: globbing the whole tree would walk data/ and .venv/.
SCHEDULE_GLOBS: Final[tuple[str, ...]] = (
    "dags/*.py",
    "airflow/dags/*.py",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    "crontab",
)
_SCHEDULE_MARKERS: Final = ("arsenal", "pugio", "gladius")
# A file is a schedule only if it names the project's tools AND declares a trigger.
_WORKFLOW_TRIGGER: Final = "schedule:"  # GitHub `on: schedule: - cron:`
_DAG_TRIGGER: Final = "schedule"  # `schedule=` and `schedule_interval=`
_NO_SCHEDULE: Final = "no schedule found in dags/, airflow/dags/, .github/workflows/ or crontab"


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _r(
    check_id: str, weight: int, status: Status, subject: str, evidence: str, fix: str
) -> CheckResult:
    return CheckResult(
        id=check_id,
        dimension=Dimension.OPERABILITY,
        weight=weight,
        status=status,
        subject=subject,
        evidence=evidence,
        fix=fix if status != Status.PASS else "",
    )


def _declares_schedule(path: Path, text: str) -> bool:
    if path.name == "crontab":
        lines = (ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        return any(m in ln for ln in lines for m in _SCHEDULE_MARKERS)
    if not any(marker in text for marker in _SCHEDULE_MARKERS):
        return False
    trigger = _DAG_TRIGGER if path.suffix == ".py" else _WORKFLOW_TRIGGER
    return trigger in text


def _schedule_file(root: Path) -> Path | None:
    for pattern in SCHEDULE_GLOBS:
        for path in sorted(root.glob(pattern)):
            try:
                text = path.read_text(encoding="utf-8").lower()
            except (OSError, UnicodeDecodeError):
                continue
            if _declares_schedule(path, text):
                return path
    return None


def check_operability(ctx: ProjectContext) -> list[CheckResult]:
    has_readme = (ctx.root / "README.md").is_file()
    schedule = _schedule_file(ctx.root)
    has_lineage = module_available("aquila")
    return [
        _r(
            "operability.readme",
            1,
            Status.PASS if has_readme else Status.FAIL,
            "README.md",
            "README.md present" if has_readme else "no README.md",
            "add a README saying what the pipeline loads and who uses it",
        ),
        _r(
            "operability.schedule",
            2,
            Status.PASS if schedule else Status.WARN,
            str(schedule.relative_to(ctx.root)) if schedule else "project",
            "runs on a schedule" if schedule else _NO_SCHEDULE,
            "schedule `arsenal run` (see examples/transfer/airflow)",
        ),
        _r(
            "operability.lineage",
            1,
            Status.PASS if has_lineage else Status.WARN,
            "aquila",
            "aquila is installed" if has_lineage else "aquila is not installed: no lineage",
            "pip install 'de-arsenal[catalog]', then `aquila scan`",
        ),
    ]
