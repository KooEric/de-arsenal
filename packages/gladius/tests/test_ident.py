"""식별자 인용/캐스트 타입 화이트리스트 (M3 Task 3.2)."""

import duckdb
import pytest

from arsenal_core.errors import FatalError
from gladius.compile.ident import normalize_type, quote_ident


@pytest.mark.parametrize("name", ["user", "이름", "col with space", 'weird"quote'])
def test_quote_roundtrips_in_duckdb(name: str) -> None:
    q = quote_ident(name)
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t ({q} INTEGER)")
    row = con.sql("SELECT column_name FROM duckdb_columns()").fetchone()
    assert row is not None
    assert row[0] == name


def test_cast_type_whitelist() -> None:
    assert normalize_type("bigint") == "BIGINT"
    with pytest.raises(FatalError, match="unsupported cast type"):
        normalize_type("bigint); DROP TABLE t; --")
