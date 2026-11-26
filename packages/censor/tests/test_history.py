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
    assert [r.run_id for r in list_reports(root).reports] == [first.run_id, second.run_id]
    changes = {c.id: c for c in diff_reports(first, second)}
    assert changes["guards.validate:collect.yaml"].before == Status.FAIL
    assert changes["guards.validate:collect.yaml"].after == Status.PASS
    assert "correctness.manifest" not in changes


def test_load_missing_run_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="no saved run"):
        load_report(tmp_path, "20260101-000000-quick")


def test_load_corrupt_run_is_fatal_not_validation_error(tmp_path: Path) -> None:
    runs = censor_dir(tmp_path) / "runs"
    runs.mkdir(parents=True)
    (runs / "bad.json").write_text("{oops")
    with pytest.raises(FatalError, match=r"saved run bad is unreadable"):
        load_report(tmp_path, "bad")


@pytest.mark.parametrize("run_id", ["../x", "a/b", "a\\b", ".hidden", ""])
def test_load_rejects_run_id_that_is_not_a_plain_stem(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(FatalError, match="invalid run id"):
        load_report(tmp_path, run_id)


def test_list_skips_corrupt_files_and_reports_them(tmp_path: Path) -> None:
    root = minimal_project(tmp_path)
    good = audit_project(root, now=T1)
    save_report(good, root, markdown="")
    bad = censor_dir(root) / "runs" / "bad.json"
    bad.write_text("{oops")
    listing = list_reports(root)
    assert [r.run_id for r in listing.reports] == [good.run_id]
    assert listing.skipped == [bad]


def test_list_without_history_dir_is_empty(tmp_path: Path) -> None:
    listing = list_reports(tmp_path)
    assert listing.reports == [] and listing.skipped == []


def test_failed_parquet_write_leaves_no_json_and_no_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise pa.ArrowInvalid("boom")

    monkeypatch.setattr("censor.history.pq.write_table", boom)
    root = minimal_project(tmp_path)
    report = audit_project(root, now=T1)
    with pytest.raises(OSError, match="boom"):
        save_report(report, root, markdown="# r\n")
    runs = censor_dir(root) / "runs"
    assert list(runs.iterdir()) == []


@pytest.mark.parametrize("link", [".arsenal", ".arsenal/censor", "runs", "reports"])
def test_save_refuses_symlinked_history_dirs(tmp_path: Path, link: str) -> None:
    root = minimal_project(tmp_path / "p")
    outside = tmp_path / "outside"
    outside.mkdir()
    base = censor_dir(root)
    target = {".arsenal": root / ".arsenal", ".arsenal/censor": base}.get(link, base / link)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError, match="symlink"):
        save_report(audit_project(root, now=T1), root, markdown="")
    assert list(outside.iterdir()) == []


def test_save_refuses_history_dir_that_resolves_outside_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = minimal_project(tmp_path / "p")
    outside = tmp_path / "outside"

    def escaped(_base: Path) -> Path:
        return outside / "censor"

    monkeypatch.setattr("censor.history.censor_dir", escaped)
    with pytest.raises(OSError, match="outside"):
        save_report(audit_project(root, now=T1), root, markdown="")
    assert not outside.exists()


def test_parquet_rows_carry_run_metadata(tmp_path: Path) -> None:
    root = minimal_project(tmp_path)
    report = audit_project(root, now=T1)
    save_report(report, root, markdown="")
    parquet = censor_dir(root) / "runs" / f"{report.run_id}.parquet"
    table = cast("pa.Table", pq.read_table(parquet))  # pyright: ignore[reportUnknownMemberType]
    rows = cast(list[dict[str, object]], table.to_pylist())
    assert {r["tier"] for r in rows} == {"quick"}
    assert {r["target_kind"] for r in rows} == {"project"}
    assert {r["target"] for r in rows} == {report.target}
    assert {r["censor_version"] for r in rows} == {report.censor_version}


def test_a_failed_save_leaves_no_half_written_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from censor import history

    report = audit_project(minimal_project(tmp_path))
    real = history._atomic_write  # pyright: ignore[reportPrivateUsage]

    def fail_on_json(path: Path, write: object) -> None:
        if path.suffix == ".json":
            raise OSError("disk full")
        real(path, write)  # type: ignore[arg-type]

    monkeypatch.setattr(history, "_atomic_write", fail_on_json)
    with pytest.raises(OSError, match="disk full"):
        save_report(report, tmp_path, markdown="x")
    censor = tmp_path / ".arsenal" / "censor"
    assert not list((censor / "runs").iterdir()) and not list((censor / "reports").iterdir())
