from pathlib import Path

from arsenal_core.spec.models import DuckDBSinkSpec, ParquetSinkSpec
from pugio.sinks import DuckDBSink, ParquetSink, build_sink


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
