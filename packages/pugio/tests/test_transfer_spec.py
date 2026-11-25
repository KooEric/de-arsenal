"""`pugio transfer` spec: validation of source, split, staging path and target."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from arsenal_core.errors import FatalError
from pugio.transfer.spec import TransferSpec, load_transfer


def raw(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "orders-to-dw",
        "source": {
            "engine": "redshift",
            "dsn_env": "SRC_DSN",
            "query": "SELECT id, amount, order_date FROM sales.orders",
            "iam_role_env": "UNLOAD_ROLE",
        },
        "staging_path": "s3://bucket/arsenal",
        "target": {
            "dsn_env": "DW_DSN",
            "table": "analytics.orders",
            "iam_role_env": "COPY_ROLE",
        },
    }
    base.update(overrides)
    return base


def test_minimal_spec_defaults_to_replace_mode_and_one_unit() -> None:
    spec = TransferSpec.model_validate(raw())
    assert spec.target.mode == "replace"
    assert spec.split is None
    assert spec.state_dir == ".arsenal"


def test_trailing_semicolon_is_removed_from_query() -> None:
    spec = TransferSpec.model_validate(
        raw(source={**raw()["source"], "query": "SELECT 1 AS x;  \n"})
    )
    assert spec.source.query == "SELECT 1 AS x"


def test_empty_query_is_refused() -> None:
    with pytest.raises(ValidationError, match="query"):
        TransferSpec.model_validate(raw(source={**raw()["source"], "query": " ; "}))


def test_date_split_accepts_day_and_month() -> None:
    spec = TransferSpec.model_validate(
        raw(
            split={
                "column": "order_date",
                "start": "2026-01-01",
                "end": "2026-03-01",
                "every": "month",
            }
        )
    )
    assert spec.split is not None
    assert spec.split.start == dt.date(2026, 1, 1)


def test_integer_split_needs_a_positive_step() -> None:
    with pytest.raises(ValidationError, match="every"):
        TransferSpec.model_validate(
            raw(split={"column": "id", "start": 0, "end": 100, "every": "day"})
        )
    with pytest.raises(ValidationError, match="every"):
        TransferSpec.model_validate(raw(split={"column": "id", "start": 0, "end": 100, "every": 0}))


def test_split_start_must_be_before_end_and_of_the_same_kind() -> None:
    with pytest.raises(ValidationError, match="before"):
        TransferSpec.model_validate(
            raw(split={"column": "d", "start": "2026-02-01", "end": "2026-01-01", "every": "day"})
        )
    with pytest.raises(ValidationError, match="same kind"):
        TransferSpec.model_validate(
            raw(split={"column": "d", "start": "2026-01-01", "end": 5, "every": "day"})
        )


def test_split_column_must_be_a_plain_identifier() -> None:
    with pytest.raises(ValidationError, match="identifier"):
        TransferSpec.model_validate(
            raw(split={"column": "d; drop", "start": 0, "end": 10, "every": 5})
        )


@pytest.mark.parametrize(
    "path", ["/local/dir", "gs://bucket/x", "s3://", "s3://bucket/it's", "s3://b/x\\y"]
)
def test_staging_path_must_be_a_plain_s3_url(path: str) -> None:
    with pytest.raises(ValidationError, match="staging_path"):
        TransferSpec.model_validate(raw(staging_path=path))


def test_merge_mode_needs_a_merge_key() -> None:
    target = {**raw()["target"], "mode": "merge"}
    with pytest.raises(ValidationError, match="merge_key"):
        TransferSpec.model_validate(raw(target=target))
    spec = TransferSpec.model_validate(raw(target={**target, "merge_key": ["id"]}))
    assert spec.target.merge_key == ["id"]


def test_hive_settings_are_restricted_to_safe_text() -> None:
    hive = {"engine": "hive", "dsn_env": "HIVE_DSN", "query": "SELECT 1"}
    spec = TransferSpec.model_validate(
        raw(source={**hive, "settings": {"tez.queue.name": "etl"}, "write_scheme": "s3a"})
    )
    assert spec.source.engine == "hive"
    with pytest.raises(ValidationError, match="settings"):
        TransferSpec.model_validate(raw(source={**hive, "settings": {"a": "b; DROP TABLE t"}}))
    with pytest.raises(ValidationError, match="settings"):
        TransferSpec.model_validate(raw(source={**hive, "settings": {"a b": "c"}}))


def test_unknown_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        TransferSpec.model_validate(raw(sink={"type": "parquet"}))


def test_load_transfer_reads_yaml_and_reports_errors(tmp_path: Path) -> None:
    good = tmp_path / "t.yaml"
    good.write_text(
        "name: t\n"
        "source: {engine: hive, dsn_env: H, query: 'SELECT 1'}\n"
        "staging_path: s3://bucket/p\n"
        "target: {dsn_env: D, table: s.t, iam_role_env: R}\n",
        encoding="utf-8",
    )
    assert load_transfer(good).name == "t"

    with pytest.raises(FatalError, match="not found"):
        load_transfer(tmp_path / "missing.yaml")

    bad = tmp_path / "bad.yaml"
    bad.write_text("name: t\nsource: {engine: oracle}\n", encoding="utf-8")
    with pytest.raises(FatalError, match="invalid transfer spec"):
        load_transfer(bad)

    broken = tmp_path / "broken.yaml"
    broken.write_text("name: [unclosed\n", encoding="utf-8")
    with pytest.raises(FatalError, match="invalid YAML"):
        load_transfer(broken)


_EXAMPLES = sorted((Path(__file__).resolve().parents[3] / "examples" / "transfer").glob("*.yaml"))


@pytest.mark.parametrize("path", _EXAMPLES, ids=[p.name for p in _EXAMPLES])
def test_transfer_examples_are_valid_specs(path: Path) -> None:
    assert load_transfer(path).name


def test_both_checks_are_on_by_default_and_names_off_is_enough_to_skip_both() -> None:
    spec = TransferSpec.model_validate(raw())
    assert spec.check_columns and spec.check_types
    # The type check compares the same positions, so it only runs with the name check.
    assert not TransferSpec.model_validate(raw(check_columns=False)).check_columns
