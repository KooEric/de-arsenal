"""Scorpio — operational freshness checks and alerts."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("de-scorpio")
except PackageNotFoundError:
    __version__ = "0.6.11"

from scorpio.freshness import Freshness, assess_freshness
from scorpio.streams import (
    RunObservation,
    RunsSummary,
    StreamHealth,
    StreamObservation,
    assess_stream,
    summarize_runs,
)

__all__ = [
    "Freshness",
    "RunObservation",
    "RunsSummary",
    "StreamHealth",
    "StreamObservation",
    "assess_freshness",
    "assess_stream",
    "summarize_runs",
]
