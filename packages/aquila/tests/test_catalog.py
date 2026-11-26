"""AQ-1..AQ-3 DoD over the three-stage fixture (see conftest)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from aquila import service
from aquila.lineage import walk
from aquila.store import CatalogStore


def write_yaml(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def catalog(tmp_path: Path) -> CatalogStore:
    return CatalogStore(tmp_path / "aquila.db")


def test_rescanning_the_same_repo_adds_no_rows(lakehouse: Path, tmp_path: Path) -> None:
    store = catalog(tmp_path)
    first = service.scan(store, [lakehouse], root=lakehouse)
    totals = store.counts()
    second = service.scan(store, [lakehouse], root=lakehouse)

    assert totals == store.counts()
    assert second.new_datasets == [] and second.new_schema_versions == []
    assert (
        first.datasets == second.datasets == 6
    )  # raw csv, orders, clean, daily, events api, events
    assert totals["edges"] == 4


def test_three_stage_lineage_crosses_tools(lakehouse: Path, tmp_path: Path) -> None:
    store = catalog(tmp_path)
    service.scan(store, [lakehouse], root=lakehouse)
    daily = store.resolve("orders_daily")
    assert daily is not None and daily.format == "iceberg"
    names = {d.dataset_id: d.name for d in store.datasets()}

    up = walk(store.edges(), daily.dataset_id, direction="up", depth=5)

    assert sorted(names[n] for n in up.nodes) == ["orders", "orders_clean", "orders_daily", "raw"]
    assert [e.via for e in up.edges] == [
        "ballista:orders_daily",
        "gladius:orders_clean",
        "pugio:orders",
    ]
    assert walk(store.edges(), daily.dataset_id, direction="up", depth=1).nodes == {
        daily.dataset_id,
        store.resolve("orders_clean").dataset_id,  # type: ignore[union-attr]
    }
    down = walk(store.edges(), store.resolve("orders").dataset_id, direction="down")  # type: ignore[union-attr]
    assert {names[n] for n in down.nodes} == {"orders", "orders_clean", "orders_daily"}


def test_any_spelling_of_a_location_resolves_to_one_dataset(
    lakehouse: Path, tmp_path: Path
) -> None:
    store = catalog(tmp_path)
    service.scan(store, [lakehouse], root=lakehouse)
    clean = store.resolve("orders_clean")

    assert clean is not None
    assert store.resolve(f"{lakehouse}/lake/clean/orders/*.parquet") == clean
    assert store.resolve(clean.dataset_id) == clean
    assert store.resolve("https://API.example.test/events?api_key=other") is not None


def test_history_diffs_schema_versions(lakehouse: Path, tmp_path: Path) -> None:
    store = catalog(tmp_path)
    service.scan(store, [lakehouse], root=lakehouse)

    timeline = service.history(store, store.resolve("orders"))  # type: ignore[arg-type]

    assert len(timeline) == 2
    assert timeline[1]["diff"] == {"added": ["email"], "removed": [], "changed": []}
    clean = service.history(store, store.resolve("orders_clean"))  # type: ignore[arg-type]
    assert clean[0]["origin"] == "parquet-footer" and clean[0]["columns"] == 2


def test_contract_change_and_violations_are_reported(lakehouse: Path, tmp_path: Path) -> None:
    store = catalog(tmp_path)
    service.scan(store, [lakehouse], root=lakehouse)
    orders = store.resolve("orders")
    assert orders is not None
    assert service.contract_findings(store, orders) == [
        "id: 1 null value(s) in data, NOT NULL in contract"
    ]

    spec = yaml.safe_load((lakehouse / "collect.yaml").read_text())
    spec["contract"]["columns"].append({"name": "customer", "type": "string"})
    write_yaml(lakehouse / "collect.yaml", spec)
    report = service.scan(store, [lakehouse], root=lakehouse)

    assert report.contract_changes == [orders.uri]
    contract = store.contracts(orders.dataset_id)[0]
    assert contract.previous_hash is not None and contract.changed_at is not None
    assert "customer: missing (contract" in service.contract_findings(store, orders)[1]


def test_tags_search_and_deprecation_warnings(lakehouse: Path, tmp_path: Path) -> None:
    store = catalog(tmp_path)
    service.scan(store, [lakehouse], root=lakehouse)
    orders = store.resolve("orders")
    assert orders is not None
    store.set_tags(orders.dataset_id, {"pii": "true", "owner": "data-team"})
    store.set_description(orders.dataset_id, "raw customer orders")

    assert [d.name for d in store.search("pii=true")] == ["orders"]
    assert [d.name for d in store.search("customer orders")] == ["orders"]
    assert {d.name for d in store.search("email")} == {"orders"}  # column names are indexed
    assert store.get(orders.dataset_id).owner == "data-team"  # type: ignore[union-attr]

    store.deprecate(orders.dataset_id, "replaced by orders_v2")
    warnings = service.deprecation_warnings(store)
    assert warnings == [
        "orders_clean reads deprecated orders (replaced by orders_v2) via gladius:orders_clean"
    ]
    clean = store.resolve("orders_clean")
    assert service.show(store, clean)["warnings"] == [  # type: ignore[arg-type]
        "reads deprecated orders (replaced by orders_v2)"
    ]
    service.scan(store, [lakehouse], root=lakehouse)
    assert store.get(orders.dataset_id).tags == {"owner": "data-team", "pii": "true"}  # type: ignore[union-attr]


def test_cycles_are_detected(tmp_path: Path) -> None:
    root = tmp_path / "loop"
    write_yaml(root / "a.yaml", {"name": "b", "input": "./a", "output": "./b"})
    write_yaml(root / "b.yaml", {"name": "a", "input": "./b", "output": "./a"})
    write_yaml(root / "c.yaml", {"name": "c", "input": "./b", "output": "./c"})
    store = catalog(tmp_path)
    service.scan(store, [root], root=root)

    assert [sorted(c) for c in service.cycle_names(store)] == [["a", "b"]]


def test_changed_input_drops_the_stale_edge(lakehouse: Path, tmp_path: Path) -> None:
    store = catalog(tmp_path)
    service.scan(store, [lakehouse], root=lakehouse)
    spec = yaml.safe_load((lakehouse / "clean.yaml").read_text())
    spec["input"] = "./lake/raw/events"
    write_yaml(lakehouse / "clean.yaml", spec)

    report = service.scan(store, [lakehouse], root=lakehouse)

    assert report.removed_edges == 1
    clean = store.resolve("orders_clean")
    assert clean is not None
    assert service.show(store, clean)["upstream"] == ["events (gladius:orders_clean)"]


def test_state_databases_are_never_modified(lakehouse: Path, tmp_path: Path) -> None:
    db = lakehouse / ".arsenal" / "orders.db"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    store = catalog(tmp_path)
    service.scan(store, [lakehouse], root=lakehouse)

    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert not (lakehouse / ".arsenal" / "backups").exists()


def test_secrets_in_source_urls_are_not_stored(lakehouse: Path, tmp_path: Path) -> None:
    store = catalog(tmp_path)
    service.scan(store, [lakehouse], root=lakehouse)
    store.close()

    assert b"sk-live-777" not in (tmp_path / "aquila.db").read_bytes()
