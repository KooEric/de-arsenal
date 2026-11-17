from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aquila.uri import dataset_id, default_name, normalize_uri


def test_relative_paths_resolve_against_the_scan_root(tmp_path: Path) -> None:
    assert (
        normalize_uri("lake/raw/orders", root=tmp_path)
        == f"file://{tmp_path.as_posix()}/lake/raw/orders"
    )


@pytest.mark.parametrize(
    "variant",
    [
        "lake/orders",
        "lake/orders/",
        "lake/orders/*.parquet",
        "lake/orders/**/*.parquet",
        "./lake/orders",
    ],
)
def test_glob_and_trailing_slash_variants_are_one_dataset(tmp_path: Path, variant: str) -> None:
    assert dataset_id(variant, root=tmp_path) == dataset_id("lake/orders", root=tmp_path)


def test_credentials_query_and_case_do_not_change_identity() -> None:
    a = normalize_uri("HTTPS://alice:pw@API.Example.test/v1/orders?api_key=x&after=9#frag")
    assert a == "https://api.example.test/v1/orders"
    assert normalize_uri("ICEBERG://analytics.daily") == "iceberg://analytics.daily"


def test_env_placeholders_are_never_expanded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_DSN", "postgres://u:secret@h/db")
    assert (
        normalize_uri("postgres://${PG_DSN}/public.orders") == "postgres://${PG_DSN}/public.orders"
    )


@pytest.mark.parametrize(
    ("uri", "name"),
    [
        ("file:///x/lake/orders", "orders"),
        ("file:///x/out/orders.parquet", "orders"),
        ("iceberg://analytics.daily", "analytics.daily"),
        ("duckdb:///x/wh.duckdb/customers", "customers"),
        ("https://api.test", "api.test"),
    ],
)
def test_default_name(uri: str, name: str) -> None:
    assert default_name(uri) == name


segment = st.text(alphabet="abcdefghij_-0123456789", min_size=1, max_size=8)


@given(st.lists(segment, min_size=1, max_size=4), st.sampled_from(["", "/", "/*.parquet", "/**"]))
def test_normalization_is_idempotent_and_suffix_insensitive(parts: list[str], suffix: str) -> None:
    root = Path("/srv/project")
    base = "/".join(parts)
    once = normalize_uri(base + suffix, root=root)
    assert once == normalize_uri(base, root=root)
    assert normalize_uri(once) == once
