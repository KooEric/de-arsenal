from pathlib import Path

from arsenal_core.spec.models import DuckDBSinkSpec, ParquetSinkSpec, PostgresSinkSpec
from pugio.sinks import DuckDBSink, ParquetSink, PostgresSink, build_sink


def test_build_sink_returns_parquet(tmp_path: Path) -> None:
    spec = ParquetSinkSpec(type="parquet", path=str(tmp_path / "out"))
    sink = build_sink(spec)
    assert isinstance(sink, ParquetSink)


def test_build_sink_returns_duckdb(tmp_path: Path) -> None:
    spec = DuckDBSinkSpec(
        type="duckdb", path=str(tmp_path / "x.duckdb"), table="t", merge_key=["id"]
    )
    sink = build_sink(spec)
    assert isinstance(sink, DuckDBSink)


def test_build_sink_returns_postgres() -> None:
    spec = PostgresSinkSpec(type="postgres", dsn_env="PG_DSN_TEST", table="t", merge_key=["id"])
    sink = build_sink(spec)
    assert isinstance(sink, PostgresSink)
