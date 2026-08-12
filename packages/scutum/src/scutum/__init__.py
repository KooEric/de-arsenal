"""Scutum — explicit data contracts for Arrow batches."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("scutum")
except PackageNotFoundError:
    __version__ = "0.2.0"

from scutum.contract import ContractColumn, ContractReport, DataContract
from scutum.lock import FileLock, LockConflict, file_lock

__all__ = [
    "ContractColumn",
    "ContractReport",
    "DataContract",
    "FileLock",
    "LockConflict",
    "file_lock",
]
