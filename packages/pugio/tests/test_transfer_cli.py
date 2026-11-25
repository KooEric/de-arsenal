"""`pugio transfer run` / `pugio transfer status` command surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from arsenal_core.errors import FatalError
from pugio import cli
from pugio.transfer.runner import FailedUnit, TransferReport, TransferStatus
from pugio.transfer.spec import TransferSpec

runner = CliRunner()


def write_spec(tmp_path: Path) -> Path:
    path = tmp_path / "transfer.yaml"
    path.write_text(
        "name: t\n"
        f"state_dir: {tmp_path / 'state'}\n"
        "source: {engine: hive, dsn_env: H, query: 'SELECT 1'}\n"
        "staging_path: s3://bucket/stage\n"
        "target: {dsn_env: D, table: s.t, iam_role_env: R}\n",
        encoding="utf-8",
    )
    return path


def test_run_prints_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def report(spec: object, reexport: bool = False) -> TransferReport:
        return TransferReport(2, 1, 1, 1, 99)

    monkeypatch.setattr(cli, "run_transfer", report)
    result = runner.invoke(cli.app, ["transfer", "run", str(write_spec(tmp_path))])
    assert result.exit_code == 0
    assert "loaded=2 skipped=1 rows=99 exported=1 reused_exports=1" in result.output


def test_run_errors_exit_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(spec: object, reexport: bool = False) -> TransferReport:
        raise FatalError("redshift copy failed for unit all: denied")

    monkeypatch.setattr(cli, "run_transfer", fail)
    result = runner.invoke(cli.app, ["transfer", "run", str(write_spec(tmp_path))])
    assert result.exit_code == 1
    assert "error: redshift copy failed" in result.output


def test_status_lists_counts_and_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    status = TransferStatus({"done": 3, "failed": 1}, [FailedUnit("d=2026-01-02..2026-01-03", "x")])

    def current(spec: object) -> TransferStatus:
        return status

    monkeypatch.setattr(cli, "transfer_status", current)
    result = runner.invoke(cli.app, ["transfer", "status", str(write_spec(tmp_path))])
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "done: 3",
        "failed: 1",
        "failed d=2026-01-02..2026-01-03: x",
    ]


def test_status_before_any_run(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["transfer", "status", str(write_spec(tmp_path))])
    assert result.exit_code == 0 and result.output.strip() == "no runs yet"


def test_invalid_spec_exits_1(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: t\n", encoding="utf-8")
    for command in ("run", "status"):
        result = runner.invoke(cli.app, ["transfer", command, str(bad)])
        assert result.exit_code == 1 and "invalid transfer spec" in result.output


def test_run_passes_the_range_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def report(spec: TransferSpec, reexport: bool = False) -> TransferReport:
        seen.append(spec.split)
        return TransferReport(1, 0, 1, 0, 3)

    path = tmp_path / "ranged.yaml"
    path.write_text(
        write_spec(tmp_path).read_text(encoding="utf-8")
        + "split: {column: d, start: 2026-01-01, end: 2026-12-31, every: day}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "run_transfer", report)
    args = ["transfer", "run", str(path), "--start", "2026-03-01", "--end", "2026-03-02"]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    [split] = seen
    assert str(split.start) == "2026-03-01" and str(split.end) == "2026-03-02"  # type: ignore[attr-defined]


def test_range_override_without_split_exits_1(tmp_path: Path) -> None:
    args = ["transfer", "run", str(write_spec(tmp_path)), "--start", "2026-03-01"]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 1 and "split" in result.output
