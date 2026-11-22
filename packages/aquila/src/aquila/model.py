"""What one scan observed, before it is merged into ``aquila.db``."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from arsenal_core.execution import canonical_hash


def schema_snapshot(fields: list[dict[str, Any]]) -> str:
    """The same JSON shape Pugio records: ``[{"name", "type", "nullable"}, ...]``."""

    return json.dumps(
        [
            {"name": str(f["name"]), "type": str(f["type"]), "nullable": bool(f["nullable"])}
            for f in fields
        ]
    )


def schema_hash(snapshot: str) -> str:
    return canonical_hash(json.loads(snapshot))


@dataclass(frozen=True)
class DatasetObservation:
    uri: str  # normalized
    format: str
    producer: str | None = None  # "pugio:orders" — the spec that writes it


@dataclass(frozen=True)
class EdgeObservation:
    upstream: str  # normalized URIs
    downstream: str
    via: str  # "gladius:orders_clean"
    revision: str  # canonical hash of the spec that declared the edge
    spec_path: str = ""  # resolved path of that spec file


@dataclass(frozen=True)
class SchemaObservation:
    uri: str
    snapshot: str
    observed_at: str  # ISO-8601 UTC
    origin: str  # "pugio-state" | "parquet-footer"


@dataclass(frozen=True)
class ContractObservation:
    uri: str
    path: str  # spec file that declares it
    contract: dict[str, Any]

    @property
    def contract_hash(self) -> str:
        return canonical_hash(self.contract)


@dataclass(frozen=True)
class FreshnessObservation:
    uri: str
    last_updated_at: str
    origin: str  # "pugio-state" | "hasta-checkpoint"


@dataclass(frozen=True)
class SourceFile:
    path: str
    kind: str  # pugio | gladius | ballista | hasta | arsenal | state | parquet
    content_hash: str


@dataclass
class ScanResult:
    publications: dict[str, dict[str, Any]] = field(default_factory=lambda: {})
    datasets: dict[str, DatasetObservation] = field(default_factory=lambda: {})
    edges: list[EdgeObservation] = field(default_factory=lambda: [])
    schemas: list[SchemaObservation] = field(default_factory=lambda: [])
    contracts: list[ContractObservation] = field(default_factory=lambda: [])
    freshness: list[FreshnessObservation] = field(default_factory=lambda: [])
    sources: list[SourceFile] = field(default_factory=lambda: [])
    skipped: list[tuple[str, str]] = field(default_factory=lambda: [])
    vias: set[str] = field(default_factory=lambda: set())  # every spec this scan read
    roots: list[str] = field(default_factory=lambda: [])  # directories scanned (resolved)
    spec_paths: set[str] = field(default_factory=lambda: set())  # spec files read (resolved)

    def dataset(self, uri: str, fmt: str, producer: str | None = None) -> None:
        known = self.datasets.get(uri)
        if known is None or (producer is not None and known.producer is None):
            self.datasets[uri] = DatasetObservation(uri, fmt, producer)
