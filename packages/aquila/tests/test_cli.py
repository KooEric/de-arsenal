from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from aquila.cli import app
from arsenal_core.state import StateStore, UnitSpec

runner = CliRunner()


def invoke(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


def scanned(lakehouse: Path, tmp_path: Path) -> str:
    db = str(tmp_path / "cat.db")
    code, out = invoke("scan", str(lakehouse), "--db", db, "--root", str(lakehouse))
    assert code == 0, out
    return db


def test_scan_reports_totals_and_is_idempotent(lakehouse: Path, tmp_path: Path) -> None:
    db = scanned(lakehouse, tmp_path)
    code, out = invoke("scan", str(lakehouse), "--db", db, "--root", str(lakehouse))

    report = json.loads(out)
    assert code == 0 and report["new_datasets"] == [] and report["totals"]["datasets"] == 6
    assert report["cycles"] == [] and report["warnings"] == []


def test_dry_run_writes_nothing(lakehouse: Path, tmp_path: Path) -> None:
    db = tmp_path / "never.db"
    code, out = invoke(
        "scan", str(lakehouse), "--db", str(db), "--root", str(lakehouse), "--dry-run"
    )

    assert code == 0 and json.loads(out)["edges"] == 4
    assert not db.exists()


def test_show_exits_one_on_contract_findings(lakehouse: Path, tmp_path: Path) -> None:
    db = scanned(lakehouse, tmp_path)
    code, out = invoke("show", "orders", "--db", db)

    payload = json.loads(out)
    assert code == 1
    assert payload["downstream"] == ["orders_clean (gladius:orders_clean)"]
    assert payload["contract_findings"] == ["id: 1 null value(s) in data, NOT NULL in contract"]

    code, out = invoke("show", "orders_clean", "--db", db)
    assert code == 0 and json.loads(out)["schema"][0]["name"] == "id"


def test_freshness_comes_from_the_pipeline_state(lakehouse: Path, tmp_path: Path) -> None:
    store = StateStore(lakehouse / ".arsenal" / "orders.db")
    unit = UnitSpec.create(pipeline="orders", source="file", unit_key="a.csv", payload={})
    store.register(unit)
    store.mark_done(unit.unit_id, row_count=1)
    store.close()
    db = scanned(lakehouse, tmp_path)

    _, out = invoke("show", "orders_clean", "--db", db, "--max-age-seconds", "1")
    assert json.loads(out)["freshness"]["status"] == "unknown"  # transforms keep no state
    _, out = invoke("show", "orders", "--db", db, "--max-age-seconds", "86400")
    fresh = json.loads(out)["freshness"]
    assert fresh["status"] == "fresh" and fresh["last_updated_at"] is not None


def test_lineage_history_tag_search_describe_deprecate(lakehouse: Path, tmp_path: Path) -> None:
    db = scanned(lakehouse, tmp_path)

    code, out = invoke(
        "lineage", "orders_daily", "--db", db, "--direction", "up", "--format", "mermaid"
    )
    assert code == 0 and out.startswith("flowchart LR") and "-->|ballista:orders_daily|" in out
    code, out = invoke("lineage", "orders", "--db", db, "--depth", "1")
    assert json.loads(out)["nodes"] == ["orders", "orders_clean", "raw"]
    code, out = invoke("history", "orders", "--db", db)
    assert json.loads(out)[1]["diff"]["added"] == ["email"]
    code, out = invoke("tag", "orders", "pii=true", "tier=bronze", "--db", db)
    assert json.loads(out) == {"pii": "true", "tier": "bronze"}
    assert invoke("describe", "orders", "landing zone for orders", "--db", db)[0] == 0
    code, out = invoke("search", "landing pii", "--db", db)
    assert [r["name"] for r in json.loads(out)] == ["orders"]
    code, out = invoke("deprecate", "orders", "--db", db, "--reason", "use orders_v2")
    assert json.loads(out)["readers"] == [
        "orders_clean reads deprecated orders (use orders_v2) via gladius:orders_clean"
    ]


def test_errors_are_one_line(lakehouse: Path, tmp_path: Path) -> None:
    assert "run `aquila scan` first" in invoke("show", "x", "--db", str(tmp_path / "missing.db"))[1]
    db = scanned(lakehouse, tmp_path)
    code, out = invoke("show", "nope", "--db", db)
    assert code == 1 and "unknown dataset 'nope'" in out
    assert invoke("tag", "orders", "pii", "--db", db)[0] == 1
    assert invoke("lineage", "orders", "--db", db, "--direction", "sideways")[0] == 1
