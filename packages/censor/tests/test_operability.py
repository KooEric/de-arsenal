from collections.abc import Callable
from pathlib import Path

import pytest
from _projects import bare_project, write_project

from censor.checks import operability
from censor.context import load_context
from censor.model import Status


def _available(value: bool) -> Callable[[str], bool]:
    def fake(name: str) -> bool:
        return value

    return fake


def _statuses(root: Path) -> dict[str, Status]:
    return {r.id: r.status for r in operability.check_operability(load_context(root))}


def test_bare_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(operability, "module_available", _available(False))
    assert _statuses(bare_project(tmp_path)) == {
        "operability.readme": Status.FAIL,
        "operability.schedule": Status.WARN,
        "operability.lineage": Status.WARN,
    }


@pytest.mark.parametrize(
    ("schedule_file", "text"),
    [
        (
            ".github/workflows/run.yml",
            "on:\n  schedule:\n    - cron: '0 3 * * *'\njobs:\n  r:\n    steps:\n"
            "      - run: arsenal run\n",
        ),
        (
            "dags/daily.py",
            "DAG('daily', schedule='@daily')\n"
            "BashOperator(task_id='run', bash_command='arsenal run')\n",
        ),
        (
            "airflow/dags/daily.py",
            "DAG('daily', schedule_interval='@daily')\nrun = 'arsenal run'\n",
        ),
        ("crontab", "0 3 * * * cd /srv/p && arsenal run\n"),
    ],
)
def test_schedule_that_runs_arsenal_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schedule_file: str, text: str
) -> None:
    monkeypatch.setattr(operability, "module_available", _available(True))
    bare_project(tmp_path)
    write_project(tmp_path, {"README.md": "# demo\n", schedule_file: text})
    assert set(_statuses(tmp_path).values()) == {Status.PASS}


@pytest.mark.parametrize(
    ("schedule_file", "text"),
    [
        (
            ".github/workflows/pr.yml",
            "on: pull_request\njobs:\n  c:\n    steps:\n      - run: arsenal check\n",
        ),
        ("dags/manual.py", "DAG('manual')\nrun = 'arsenal run'\n"),
        ("crontab", "# 0 3 * * * arsenal run\n0 3 * * * backup.sh\n"),
    ],
)
def test_arsenal_file_without_a_schedule_trigger_warns(
    tmp_path: Path, schedule_file: str, text: str
) -> None:
    bare_project(tmp_path)
    write_project(tmp_path, {schedule_file: text})
    assert _statuses(tmp_path)["operability.schedule"] == Status.WARN


def test_schedule_file_that_never_mentions_arsenal_still_warns(tmp_path: Path) -> None:
    bare_project(tmp_path)
    write_project(tmp_path, {"dags/other.py": "print('hello')\n"})
    assert _statuses(tmp_path)["operability.schedule"] == Status.WARN
