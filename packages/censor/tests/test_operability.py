from pathlib import Path

import pytest
from _projects import bare_project, write_project

from censor.checks import operability
from censor.context import load_context
from censor.model import Status


def _statuses(root: Path) -> dict[str, Status]:
    return {r.id: r.status for r in operability.check_operability(load_context(root))}


def test_bare_project(tmp_path: Path) -> None:
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
def test_schedule_that_runs_arsenal_passes(tmp_path: Path, schedule_file: str, text: str) -> None:
    bare_project(tmp_path)
    write_project(
        tmp_path, {"README.md": "# demo\n", schedule_file: text, ".arsenal/aquila.db": ""}
    )
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


def _lineage(root: Path) -> tuple[Status, str]:
    results = {r.id: r for r in operability.check_operability(load_context(root))}
    lineage = results["operability.lineage"]
    return lineage.status, lineage.evidence


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        (".arsenal/aquila.db", ""),
        ("pyproject.toml", '[project]\ndependencies = ["de-aquila>=0.6,<0.7"]\n'),
        ("pyproject.toml", 'dependencies = ["de-arsenal[catalog]>=0.6"]\n'),
        ("requirements.txt", "de-arsenal[redshift, catalog]==0.6.16\n"),
        ("requirements-dev.txt", "pytest\nde-aquila\n"),
    ],
)
def test_lineage_passes_on_project_evidence(tmp_path: Path, rel: str, text: str) -> None:
    bare_project(tmp_path)
    write_project(tmp_path, {rel: text})
    status, evidence = _lineage(tmp_path)
    assert status == Status.PASS
    assert rel in evidence


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("pyproject.toml", 'dependencies = ["de-arsenal>=0.6"]\n'),
        ("requirements.txt", "# de-aquila\nde-arsenal[redshift]\n"),
        ("sub/requirements.txt", "de-aquila\n"),
        (".aquila.db", ""),
    ],
)
def test_lineage_warns_without_project_evidence(tmp_path: Path, rel: str, text: str) -> None:
    bare_project(tmp_path)
    write_project(tmp_path, {rel: text})
    assert _lineage(tmp_path)[0] == Status.WARN


def test_lineage_ignores_the_auditors_own_environment(tmp_path: Path) -> None:
    import importlib.util

    assert importlib.util.find_spec("aquila") is not None  # installed in this workspace
    assert _lineage(bare_project(tmp_path))[0] == Status.WARN


def test_a_commented_out_schedule_is_not_a_schedule(tmp_path: Path) -> None:
    from censor.checks.operability import _schedule_file  # pyright: ignore[reportPrivateUsage]

    dag = tmp_path / "dags" / "daily.py"
    dag.parent.mkdir()
    dag.write_text(
        "# schedule='@daily' goes here later\nBashOperator(bash_command='arsenal run')\n"
    )
    assert _schedule_file(tmp_path) is None
    dag.write_text(
        "DAG('d', schedule='@daily')  # nightly\nBashOperator(bash_command='arsenal run')\n"
    )
    assert _schedule_file(tmp_path) == dag
