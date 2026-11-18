"""Aquila — the legion standard: one file that says what data exists and where it came from.

Pull model (AQ-ADR-1): ``aquila scan`` reads pipeline/transform/stream YAML, the
tools' state databases (read-only) and Parquet footers, and records datasets,
schema versions, lineage edges and contracts in a separate ``aquila.db``
(AQ-ADR-2). Nothing else depends on Aquila; removing it breaks nothing.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("de-aquila")
except PackageNotFoundError:
    __version__ = "0.5.3"

from aquila.uri import dataset_id, normalize_uri

__all__ = ["__version__", "dataset_id", "normalize_uri"]
