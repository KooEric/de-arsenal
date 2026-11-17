"""AQ-4 DoD: Augur gives the same eval results with or without an Aquila catalog."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import augur.cli as cli
from aquila import service
from aquila.store import CatalogStore
from arsenal_core.errors import FatalError
from augur.aquila_source import tables_from_aquila
from augur.catalog import Catalog, build_catalog
from augur.evaluate import load_cases, run_eval
from augur.fixture import TABLE_NAMES
from augur.llm import FakeProvider

CASES = Path(__file__).resolve().parents[1] / "eval" / "cases.yaml"


def aquila_db(tmp_path: Path, data_dir: Path) -> Path:
    specs = tmp_path / "specs"
    specs.mkdir()
    for name in TABLE_NAMES:
        spec = {
            "name": name,
            "source": {"type": "file", "path": f"./raw/{name}.csv"},
            "sink": {"type": "parquet", "path": str(data_dir / name)},
        }
        (specs / f"{name}.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    db = tmp_path / "aquila.db"
    store = CatalogStore(db)
    service.scan(store, [specs], root=tmp_path)
    store.close()
    return db


def test_aquila_tables_match_the_hand_written_sources(tmp_path: Path, data_dir: Path) -> None:
    tables = tables_from_aquila(aquila_db(tmp_path, data_dir))

    assert tables == {n: f"{data_dir.as_posix()}/{n}/*.parquet" for n in TABLE_NAMES}


def test_eval_results_are_identical_with_and_without_aquila(
    tmp_path: Path, data_dir: Path, catalog: Catalog
) -> None:
    from_aquila = build_catalog(tables_from_aquila(aquila_db(tmp_path, data_dir)))
    cases = tuple(
        c.model_copy(update={"golden_sql": c.golden_sql.replace("./data", str(data_dir))})
        for c in load_cases(CASES)
    )
    assert len(cases) == 34
    golden = FakeProvider({c.question: c.golden_sql for c in cases})

    def outcomes(cat: Catalog, run: str) -> list[tuple[str, str, str]]:
        results = run_eval(cases, cat, golden, run_id=run, out_dir=tmp_path / run)
        return [(r.case_id, r.failure_mode, r.retrieved_tables) for r in results]

    assert outcomes(from_aquila, "aquila") == outcomes(catalog, "yaml")


def test_index_reads_aquila_and_reports_a_missing_catalog(tmp_path: Path, data_dir: Path) -> None:
    out = tmp_path / "catalog.json"
    runner = CliRunner()
    db = aquila_db(tmp_path, data_dir)

    result = runner.invoke(cli.app, ["index", "--aquila", str(db), "--catalog", str(out)])
    assert result.exit_code == 0, result.output
    assert [t.table for t in Catalog.load(out).tables] == sorted(TABLE_NAMES)

    missing = runner.invoke(cli.app, ["index", "--aquila", str(tmp_path / "nope.db")])
    assert missing.exit_code == 1 and "aquila catalog not found" in missing.output
    with pytest.raises(FatalError):
        tables_from_aquila(tmp_path / "nope.db")


def test_index_without_sources_or_catalog_explains_both_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.app, ["index"])

    assert result.exit_code == 1 and "--aquila" in result.output
