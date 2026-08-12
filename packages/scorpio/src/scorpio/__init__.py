"""Scorpio — operational freshness checks and alerts."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("scorpio")
except PackageNotFoundError:
    __version__ = "0.2.0"

from scorpio.freshness import Freshness, assess_freshness

__all__ = ["Freshness", "assess_freshness"]
