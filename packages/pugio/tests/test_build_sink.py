import sys
from pathlib import Path

import pytest

from arsenal_core.errors import FatalError
from arsenal_core.spec.models import DuckDBSinkSpec, ParquetSinkSpec, PostgresSinkSpec
from pugio.sinks import (
    DuckDBSink,
    ObjectStorageParquetSink,
    ParquetSink,
    PostgresSink,
    build_sink,
)


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


@pytest.mark.parametrize(
    "path", ["s3://bucket/prefix", "gcs://bucket/prefix", "gs://bucket/prefix"]
)
def test_build_sink_returns_object_storage_parquet(path: str) -> None:
    sink = build_sink(ParquetSinkSpec(type="parquet", path=path))
    assert isinstance(sink, ObjectStorageParquetSink)


def test_build_sink_returns_postgres() -> None:
    spec = PostgresSinkSpec(type="postgres", dsn_env="PG_DSN_TEST", table="t", merge_key=["id"])
    sink = build_sink(spec)
    assert isinstance(sink, PostgresSink)


def test_build_sink_postgres_without_psycopg_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.1.0 릴리스 블로커 회귀 테스트: psycopg가 없는 환경에서 postgres sink를
    요청하면 raw `ModuleNotFoundError`가 아니라 안내 메시지를 담은 `FatalError`가
    나야 한다 (`pugio[postgres]` extra 미설치 시 `de-arsenal` 문서화된 설치 경로가
    깨지는 문제, M4 최종 리뷰에서 발견).

    `sys.modules["psycopg"] = None`은 파이썬 임포트 시스템이 이후의
    `import psycopg`를 즉시 `ImportError`로 처리하게 만드는 표준 트릭이다.
    `pugio.sinks.postgres`가 이미 다른 테스트(`test_build_sink_returns_postgres`
    등)에서 성공적으로 import되어 `sys.modules`에 캐시돼 있을 수 있으므로, 그
    캐시도 지워서 `build_sink`의 지연 import가 실제로 모듈을 다시 실행하며
    (모듈 최상단의 `import psycopg`에서) 실패를 재현하게 한다. `monkeypatch`가
    테스트 종료 시 두 `sys.modules` 항목을 원래 상태로 자동 복원하므로 다른
    테스트를 오염시키지 않는다.
    """
    monkeypatch.setitem(sys.modules, "psycopg", None)
    monkeypatch.delitem(sys.modules, "pugio.sinks.postgres", raising=False)

    spec = PostgresSinkSpec(type="postgres", dsn_env="PG_DSN_TEST", table="t", merge_key=["id"])

    with pytest.raises(FatalError, match=r"postgres.*extra"):
        build_sink(spec)
