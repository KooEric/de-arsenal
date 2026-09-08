from pathlib import Path

from augur.catalog import Catalog


def test_catalog_has_three_tables_with_samples(catalog: Catalog) -> None:
    assert [d.table for d in catalog.tables] == ["customers", "orders", "products"]
    orders = catalog.get("orders")
    assert orders is not None and orders.row_count == 400
    status = next(c for c in orders.columns if c.name == "status")
    assert set(status.samples) == {"paid", "pending", "refunded", "cancelled"}
    amount = next(c for c in orders.columns if c.name == "amount")
    assert amount.samples == ()  # 숫자 컬럼은 샘플 없음


def test_render_is_deterministic(catalog: Catalog) -> None:
    doc = catalog.get("orders")
    assert doc is not None
    assert doc.render() == doc.render()
    assert "TABLE orders" in doc.render() and "status VARCHAR e.g." in doc.render()


def test_save_load_roundtrip(catalog: Catalog, tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    catalog.save(p)
    assert Catalog.load(p) == catalog
