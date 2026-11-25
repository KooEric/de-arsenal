"""Unit planning for `pugio transfer`: ranges, predicates, staging prefixes, identity."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pugio.transfer.spec import TransferSpec
from pugio.transfer.units import plan_units


def spec(
    split: dict[str, Any] | None = None, query: str = "SELECT * FROM t", table: str = "s.t"
) -> TransferSpec:
    return TransferSpec.model_validate(
        {
            "name": "t",
            "source": {"engine": "hive", "dsn_env": "H", "query": query},
            "split": split,
            "staging_path": "s3://bucket/stage/",
            "target": {"dsn_env": "D", "table": table, "iam_role_env": "R"},
        }
    )


def test_without_split_there_is_one_unit_over_the_whole_query() -> None:
    [unit] = plan_units(spec())
    assert unit.predicate is None
    assert unit.unit.unit_key == "all"
    assert unit.uri == f"s3://bucket/stage/t/{unit.unit.unit_id}/"


def test_day_split_is_half_open_and_ends_at_end() -> None:
    units = plan_units(
        spec({"column": "d", "start": "2026-01-30", "end": "2026-02-02", "every": "day"})
    )
    assert [u.unit.unit_key for u in units] == [
        "d=2026-01-30..2026-01-31",
        "d=2026-01-31..2026-02-01",
        "d=2026-02-01..2026-02-02",
    ]
    assert units[0].predicate == "d >= DATE '2026-01-30' AND d < DATE '2026-01-31'"


def test_month_split_clips_the_last_range_to_end() -> None:
    units = plan_units(
        spec({"column": "d", "start": "2026-01-15", "end": "2026-03-10", "every": "month"})
    )
    assert [(u.lo, u.hi) for u in units] == [
        (dt.date(2026, 1, 15), dt.date(2026, 2, 1)),
        (dt.date(2026, 2, 1), dt.date(2026, 3, 1)),
        (dt.date(2026, 3, 1), dt.date(2026, 3, 10)),
    ]


def test_integer_split_steps_and_clips() -> None:
    units = plan_units(spec({"column": "id", "start": 0, "end": 25, "every": 10}))
    assert [(u.lo, u.hi) for u in units] == [(0, 10), (10, 20), (20, 25)]
    assert units[2].predicate == "id >= 20 AND id < 25"


def test_unit_ids_are_stable_and_follow_the_query() -> None:
    first = [u.unit.unit_id for u in plan_units(spec())]
    again = [u.unit.unit_id for u in plan_units(spec())]
    changed = [u.unit.unit_id for u in plan_units(spec(query="SELECT a FROM t"))]
    assert first == again
    assert first != changed  # a new query must not reuse units loaded by the old one


def test_unit_query_wraps_the_user_query_with_the_range() -> None:
    [unit] = plan_units(spec({"column": "id", "start": 0, "end": 5, "every": 5}, "SELECT * FROM t"))
    assert unit.query("SELECT * FROM t") == (
        "SELECT * FROM (\nSELECT * FROM t\n) arsenal_src WHERE id >= 0 AND id < 5"
    )
    [whole] = plan_units(spec())
    assert whole.query("SELECT * FROM t") == "SELECT * FROM t"


def test_unit_ids_follow_the_target() -> None:
    first = [u.unit.unit_id for u in plan_units(spec())]
    moved = [u.unit.unit_id for u in plan_units(spec(table="s.t_rebuilt"))]
    assert first != moved  # a new target must not be skipped as already loaded


def test_a_trailing_line_comment_does_not_swallow_the_range() -> None:
    [unit] = plan_units(spec({"column": "id", "start": 0, "end": 5, "every": 5}))
    wrapped = unit.query("SELECT * FROM t -- paid only")
    assert wrapped.endswith("\n) arsenal_src WHERE id >= 0 AND id < 5")
