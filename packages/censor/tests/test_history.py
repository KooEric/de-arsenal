from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from _projects import minimal_project, production_project

from arsenal_core.errors import FatalError
from censor.audit import audit_project
from censor.history import censor_dir, diff_reports, list_reports, load_report, save_report
from censor.model import Status

T1 = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)


def test_save_writes_json_parquet_markdown_and_gitignore(tmp_path: Path) -> None:
    root = minimal_project(tmp_path)
    report = audit_project(root, now=T1)
    path = save_report(report, root, markdown="# r\n")
    base = censor_dir(root)
    assert path == base / "runs" / f"{report.run_id}.json"
    assert (base / "reports" / f"{report.run_id}.md").read_text() == "# r\n"
    assert (base / ".gitignore").read_text() == "*\n"
    parquet = base / "runs" / f"{report.run_id}.parquet"
    table = cast("pa.Table", pq.read_table(parquet))  # pyright: ignore[reportUnknownMemberType]
    assert table.num_rows == len(report.checks)
    assert set(table.column_names) >= {"run_id", "id", "dimension", "status", "weight"}
    assert load_report(root, report.run_id) == report


def test_existing_gitignore_is_left_alone(tmp_path: Path) -> None:
    root = minimal_project(tmp_path)
    base = censor_dir(root)
    base.mkdir(parents=True)
    (base / ".gitignore").write_text("custom\n")
    save_report(audit_project(root, now=T1), root, markdown="")
    assert (base / ".gitignore").read_text() == "custom\n"


def test_list_and_diff(tmp_path: Path) -> None:
    root = minimal_project(tmp_path / "p")
    first = audit_project(root, now=T1)
    save_report(first, root, markdown="")
    production_project(root)
    second = audit_project(root, now=T2)
    save_report(second, root, markdown="")
    assert [r.run_id for r in list_reports(root)] == [first.run_id, second.run_id]
    changes = {c.id: c for c in diff_reports(first, second)}
    assert changes["guards.validate:collect.yaml"].before == Status.FAIL
    assert changes["guards.validate:collect.yaml"].after == Status.PASS
    assert "correctness.manifest" not in changes


def test_load_missing_run_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="no saved run"):
        load_report(tmp_path, "20260101-000000-quick")
