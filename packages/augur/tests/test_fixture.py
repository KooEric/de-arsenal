from pathlib import Path

import duckdb

from augur.fixture import N_CUSTOMERS, N_ORDERS, make


def test_fixture_is_deterministic(tmp_path: Path) -> None:
    make(tmp_path / "a")
    make(tmp_path / "b")
    for name, n in (("customers", N_CUSTOMERS), ("orders", N_ORDERS)):
        a = duckdb.sql(f"SELECT * FROM '{tmp_path}/a/{name}/*.parquet' ORDER BY 1").fetchall()
        b = duckdb.sql(f"SELECT * FROM '{tmp_path}/b/{name}/*.parquet' ORDER BY 1").fetchall()
        assert a == b and len(a) == n
