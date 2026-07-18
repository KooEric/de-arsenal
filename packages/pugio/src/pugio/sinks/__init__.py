"""Sink 팩토리 — SinkSpec.type으로 알맞은 구현체를 만든다 (M2-F Task F1).

discriminated union이라 spec.type이 좁혀지면 pyright도 필드를 좁혀 안다.
runner.py의 임시 parquet-only 가드(M2-A)를 이 팩토리가 대체한다.

postgres 분기는 Task F3에서 PostgresSink 구현이 들어오기 전까지 임시 가드다
(M2-A 가드와 같은 성격 — 이번엔 postgres 하나로 범위를 좁힌 것).
"""

from pathlib import Path

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import SinkSpec
from pugio.sinks.base import Sink
from pugio.sinks.duckdb import DuckDBSink
from pugio.sinks.parquet import ParquetSink


def build_sink(spec: SinkSpec) -> Sink:
    if spec.type == "parquet":
        # spec.path는 str(URI 스킴 보존용) — 로컬 파일시스템 싱크는 여기서 Path로 감싼다.
        return ParquetSink(Path(spec.path))
    if spec.type == "duckdb":
        return DuckDBSink(spec)
    if spec.type == "postgres":
        raise FatalError("postgres sink not yet implemented (M2-F Task F3)")
    raise FatalError(f"unknown sink type: {spec.type}")  # pragma: no cover


__all__ = ["DuckDBSink", "ParquetSink", "Sink", "build_sink"]
