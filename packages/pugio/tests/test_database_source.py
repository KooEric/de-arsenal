"""DatabaseSource 초안(draft) 테스트 — sqlite3 드라이버.

주의(draft deviation): 원안은 DuckDB scanner(ATTACH)로 postgres/mysql까지
아우르지만, 이 초안은 네트워크 없이 즉시 동작하도록 표준 sqlite3만 구현했다.
"""

import sqlite3
from pathlib import Path

import pytest

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import DatabaseSourceSpec, SplitSpec
from arsenal_core.state import UnitSpec
from pugio.sources.database import DatabaseSource


def make_db(tmp_path: Path, n: int, *, name: str = "src.db") -> Path:
    db = tmp_path / name
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO orders VALUES (?, ?)", [(i, "x") for i in range(1, n + 1)])
    con.commit()
    con.close()
    return db


def make_spec(*, chunk: int = 100) -> DatabaseSourceSpec:
    return DatabaseSourceSpec(
        type="database",
        dialect="sqlite",
        dsn_env="SRC_DB",
        table="orders",
        split=SplitSpec(key="id", chunk=chunk),
    )


def test_units_are_key_ranges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = make_db(tmp_path, 250)
    monkeypatch.setenv("SRC_DB", str(db))
    src = DatabaseSource(make_spec(), pipeline="p")
    units = list(src.units())
    assert [u.unit_key for u in units] == ["id=1..101", "id=101..201", "id=201..301"]


def test_fetch_range_returns_arrow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = make_db(tmp_path, 250)
    monkeypatch.setenv("SRC_DB", str(db))
    src = DatabaseSource(make_spec(), pipeline="p")
    unit = next(iter(src.units()))
    result = src.fetch(unit)
    assert result.batch is not None
    assert result.batch.num_rows == 100
    assert result.exhausted is False
    assert sorted(row["id"] for row in result.batch.to_pylist()) == list(range(1, 101))


def test_last_partial_range_returns_remaining_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = make_db(tmp_path, 250)
    monkeypatch.setenv("SRC_DB", str(db))
    src = DatabaseSource(make_spec(), pipeline="p")
    units = list(src.units())
    result = src.fetch(units[-1])
    assert result.batch is not None
    assert result.batch.num_rows == 50  # 201..250 (범위 끝은 max+1을 넘어감)


def test_rerun_with_grown_table_only_adds_new_ranges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = make_db(tmp_path, 250)
    monkeypatch.setenv("SRC_DB", str(db))
    first_units = list(DatabaseSource(make_spec(), pipeline="p").units())

    con = sqlite3.connect(db)
    con.executemany("INSERT INTO orders VALUES (?, ?)", [(i, "x") for i in range(251, 551)])
    con.commit()
    con.close()

    second_units = list(DatabaseSource(make_spec(), pipeline="p").units())
    first_keys = [u.unit_key for u in first_units]
    second_keys = [u.unit_key for u in second_units]
    # 기존 범위는 그대로(unit_id도 동일 — 재실행 시 done으로 skip됨), 새 범위만 추가
    assert second_keys[: len(first_keys)] == first_keys
    assert [u.unit_id for u in second_units[: len(first_units)]] == [u.unit_id for u in first_units]
    assert len(second_keys) > len(first_keys)


def test_empty_table_has_no_units(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = make_db(tmp_path, 0)
    monkeypatch.setenv("SRC_DB", str(db))
    src = DatabaseSource(make_spec(), pipeline="p")
    assert list(src.units()) == []


def test_missing_dsn_env_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SRC_DB", raising=False)
    src = DatabaseSource(make_spec(), pipeline="p")
    with pytest.raises(FatalError, match="SRC_DB"):
        list(src.units())


def test_unsupported_dialect_is_fatal() -> None:
    spec = DatabaseSourceSpec(
        type="database",
        dialect="postgres",
        dsn_env="SRC_DB",
        table="orders",
        split=SplitSpec(key="id", chunk=100),
    )
    with pytest.raises(FatalError, match="sqlite"):
        DatabaseSource(spec, pipeline="p")


def test_fetch_range_with_no_matching_rows_returns_no_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = make_db(tmp_path, 10)
    monkeypatch.setenv("SRC_DB", str(db))
    src = DatabaseSource(make_spec(), pipeline="p")
    unit = UnitSpec.create(
        pipeline="p", source="orders", unit_key="id=1000..1100", payload={"lo": 1000, "hi": 1100}
    )
    result = src.fetch(unit)
    assert result.batch is None
    assert result.exhausted is False


def test_malicious_split_key_is_rejected_as_invalid_identifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = make_db(tmp_path, 10)
    monkeypatch.setenv("SRC_DB", str(db))
    spec = DatabaseSourceSpec(
        type="database",
        dialect="sqlite",
        dsn_env="SRC_DB",
        table="orders",
        split=SplitSpec(key="id; DROP TABLE orders;--", chunk=100),
    )
    src = DatabaseSource(spec, pipeline="p")
    with pytest.raises(FatalError, match="invalid identifier"):
        list(src.units())
