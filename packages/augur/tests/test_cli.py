"""augur CLI — index / ask / eval / report. 프로바이더는 FakeProvider로 갈아끼운다."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import augur.cli as cli
from augur.catalog import Catalog
from augur.evaluate import load_cases
from augur.fixture import TABLE_NAMES
from augur.llm import FakeProvider, Provider

runner = CliRunner()
CASES = Path(__file__).resolve().parents[1] / "eval" / "cases.yaml"


def _sources(tmp_path: Path, data_dir: Path) -> Path:
    p = tmp_path / "sources.yaml"
    p.write_text(
        "tables:\n" + "".join(f"  {n}: {data_dir}/{n}/*.parquet\n" for n in TABLE_NAMES),
        encoding="utf-8",
    )
    return p


def _index(tmp_path: Path, data_dir: Path) -> Path:
    catalog = tmp_path / "catalog.json"
    result = runner.invoke(
        cli.app, ["index", str(_sources(tmp_path, data_dir)), "--catalog", str(catalog)]
    )
    assert result.exit_code == 0, result.output
    return catalog


def _patch_provider(monkeypatch: pytest.MonkeyPatch, fake: FakeProvider) -> None:
    def factory(name: str, model: str | None = None) -> Provider:
        return fake

    monkeypatch.setattr(cli, "provider_from_name", factory)


def test_index_then_ask_prints_rows(
    tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _index(tmp_path, data_dir)
    orders = Catalog.load(catalog).get("orders")
    assert orders is not None
    fake = FakeProvider({"How many": f"SELECT count(*) FROM '{orders.path}'"})
    _patch_provider(monkeypatch, fake)
    traces = tmp_path / "traces.jsonl"
    result = runner.invoke(
        cli.app,
        ["ask", "How many orders are there?", "--catalog", str(catalog), "--traces", str(traces)],
    )
    assert result.exit_code == 0, result.output
    assert '["400"]' in result.output
    assert traces.read_text(encoding="utf-8").count("\n") == 1


def test_ask_non_select_exits_1(
    tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _index(tmp_path, data_dir)
    _patch_provider(monkeypatch, FakeProvider({}, default="DROP TABLE orders"))
    traces = tmp_path / "t.jsonl"
    result = runner.invoke(
        cli.app, ["ask", "Drop it", "--catalog", str(catalog), "--traces", str(traces)]
    )
    assert result.exit_code == 1
    assert "NON_SELECT" in result.output


def test_ask_without_catalog_exits_1(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["ask", "q", "--catalog", str(tmp_path / "missing.json")])
    assert result.exit_code == 1
    assert "augur index" in result.output


def test_index_bad_sources_exits_1(tmp_path: Path) -> None:
    bad = tmp_path / "sources.yaml"
    bad.write_text("nothing: here\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["index", str(bad), "--catalog", str(tmp_path / "c.json")])
    assert result.exit_code == 1


def test_eval_and_report(tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _index(tmp_path, data_dir)
    cases = tmp_path / "cases.yaml"
    cases.write_text(
        CASES.read_text(encoding="utf-8").replace("./data", str(data_dir)), encoding="utf-8"
    )
    # 오라클: 항상 골든을 그대로 답한다 → 함정(answerable: false)만 빼고 CORRECT여야 한다.
    oracle = FakeProvider({c.question: c.golden_sql for c in load_cases(cases)})
    _patch_provider(monkeypatch, oracle)
    out = tmp_path / "eval"
    args = ["eval", str(cases), "--catalog", str(catalog), "--out", str(out), "--run-id", "oracle"]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert "run_id=oracle" in result.output and "CORRECT" in result.output
    assert (out / "oracle.parquet").exists()
    result = runner.invoke(cli.app, ["report", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "oracle" in result.output
