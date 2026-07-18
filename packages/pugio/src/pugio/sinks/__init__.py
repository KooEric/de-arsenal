"""Sink 팩토리 — SinkSpec.type으로 알맞은 구현체를 만든다 (M2-F Task F1).

discriminated union이라 spec.type이 좁혀지면 pyright도 필드를 좁혀 안다.
runner.py의 임시 parquet-only 가드(M2-A)를 이 팩토리가 대체한다.

M4 패키징 검증(clean-venv smoke test)에서 발견: `postgres` extra(`psycopg`)는
optional(`pugio[postgres]`)인데 이 모듈이 `PostgresSink`를 최상단에서 import하면
`pugio` 최소 설치(`pip install pugio`)만으로도 `import pugio.sinks` → `import
pugio.sinks.postgres` → `import psycopg`가 연쇄되어 psycopg 미설치 환경에서
`pugio --help`조차 ModuleNotFoundError로 죽는다 — sources/file.py의 fastexcel이
이미 쓰는 지연 import 패턴(필요할 때만 import, 실패 시 안내 메시지)을 여기도
적용한다. `TYPE_CHECKING` 임포트로 정적 타입은 그대로 유지하고, 런타임은
`build_sink`의 postgres 분기와 모듈 `__getattr__`에서만 실제로 import한다.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import SinkSpec
from pugio.sinks.base import Sink
from pugio.sinks.duckdb import DuckDBSink
from pugio.sinks.parquet import ParquetSink

if TYPE_CHECKING:
    from pugio.sinks.postgres import PostgresSink


def build_sink(spec: SinkSpec) -> Sink:
    if spec.type == "parquet":
        # spec.path는 str(URI 스킴 보존용) — 로컬 파일시스템 싱크는 여기서 Path로 감싼다.
        return ParquetSink(Path(spec.path))
    if spec.type == "duckdb":
        return DuckDBSink(spec)
    if spec.type == "postgres":
        from pugio.sinks.postgres import PostgresSink as _PostgresSink

        return _PostgresSink(spec)
    raise FatalError(f"unknown sink type: {spec.type}")  # pragma: no cover


def __getattr__(name: str) -> Any:  # noqa: ANN401 - PEP 562 lazy re-export
    """`from pugio.sinks import PostgresSink`를 psycopg 설치 시에만 지연 해결한다."""
    if name == "PostgresSink":
        from pugio.sinks.postgres import PostgresSink

        return PostgresSink
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["DuckDBSink", "ParquetSink", "PostgresSink", "Sink", "build_sink"]
