"""스켈레톤 배선 검증 — 공개 API가 전부 import 가능하고 이름이 살아 있어야 한다."""

import pugio
from pugio.auth import AuthProvider
from pugio.cli import app
from pugio.runner import RunReport, run_pipeline
from pugio.sinks import ParquetSink, Sink
from pugio.sources import FetchResult, RestSource, Source


def test_version() -> None:
    assert pugio.__version__


def test_public_api_is_wired() -> None:
    assert callable(run_pipeline)
    assert app.info is not None  # typer app 존재
    wired = (AuthProvider, RunReport, ParquetSink, Sink, FetchResult, RestSource, Source)
    assert all(cls is not None for cls in wired)
