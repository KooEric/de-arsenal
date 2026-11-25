"""Ballista capability-gated remote execution lifecycle."""

from importlib.metadata import PackageNotFoundError, version

from ballista.artifacts import ArtifactRepository, RunManifest
from ballista.config import BallistaProfile, load_profile
from ballista.executors import RemoteExecutor, SparkKubernetesExecutor
from ballista.preflight import PreflightError, PreflightReport
from ballista.runner import BallistaRunner

try:
    __version__ = version("de-ballista")
except PackageNotFoundError:
    __version__ = "0.6.17"

__all__ = [
    "ArtifactRepository",
    "BallistaProfile",
    "BallistaRunner",
    "PreflightError",
    "PreflightReport",
    "RemoteExecutor",
    "RunManifest",
    "SparkKubernetesExecutor",
    "load_profile",
]
