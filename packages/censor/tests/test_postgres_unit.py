import pytest

from arsenal_core.errors import FatalError
from censor.postgres import redact, split_table, with_statement_timeout
from censor.target import PostgresTarget

TARGET = PostgresTarget("public.orders", "CENSOR_TEST_DSN")


def test_statement_timeout_is_added_to_both_dsn_forms() -> None:
    url = with_statement_timeout("postgresql://u:pw@h:5432/db", 1.5)
    assert url == "postgresql://u:pw@h:5432/db?options=-c%20statement_timeout%3D1500"
    assert with_statement_timeout("postgresql://h/db?sslmode=require", 2).endswith(
        "?sslmode=require&options=-c%20statement_timeout%3D2000"
    )
    keyword = with_statement_timeout("host=h dbname=db", 120)
    assert keyword == "host=h dbname=db options='-c statement_timeout=120000'"
    assert with_statement_timeout("host=h", 0.0001).endswith("statement_timeout=1'")
    own = "postgresql://h/db?options=-c%20search_path%3Dx"
    assert with_statement_timeout(own, 5) == own


def test_redact_removes_the_dsn_and_its_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CENSOR_TEST_DSN", "postgresql://app:s3cr3t-pw@db.internal:5432/prod")
    text = redact(
        'IO Error: could not connect "postgresql://app:s3cr3t-pw@db.internal:5432/prod'
        '?options=-c%20statement_timeout%3D1000": password s3cr3t-pw rejected',
        TARGET,
    )
    assert "s3cr3t-pw" not in text and "db.internal" not in text
    monkeypatch.setenv("CENSOR_TEST_DSN", "host=h password='two words' dbname=d")
    assert "two words" not in redact("bad password 'two words' for h", TARGET)
    monkeypatch.delenv("CENSOR_TEST_DSN")
    assert redact("unchanged", TARGET) == "unchanged"


def test_table_names_are_validated() -> None:
    assert split_table("orders") == ("public", "orders")
    assert split_table("sales.orders") == ("sales", "orders")
    for bad in ("orders; DROP TABLE x", 'a"b', "a.b.c", ""):
        with pytest.raises(FatalError, match="invalid table name"):
            split_table(bad)
