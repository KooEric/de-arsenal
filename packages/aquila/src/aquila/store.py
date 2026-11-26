"""``aquila.db`` — the catalog's own SQLite file, never a pipeline state DB (AQ-ADR-2).

Every write is an upsert keyed by a deterministic id, so scanning the same
inputs twice adds no rows (AQ-ADR-3).
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aquila.model import ScanResult, schema_hash
from aquila.uri import dataset_id, default_name

SCHEMA_VERSION = 4

_V1 = (
    """CREATE TABLE datasets (
        dataset_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, uri TEXT NOT NULL UNIQUE,
        format TEXT NOT NULL, producer TEXT, owner TEXT, status TEXT NOT NULL DEFAULT 'active',
        deprecation_reason TEXT, description TEXT, last_updated_at TEXT, freshness_origin TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE dataset_tags (
        dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id), key TEXT NOT NULL,
        value TEXT NOT NULL, PRIMARY KEY (dataset_id, key))""",
    """CREATE TABLE schema_versions (
        dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id), schema_hash TEXT NOT NULL,
        snapshot_json TEXT NOT NULL, origin TEXT NOT NULL, first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL, PRIMARY KEY (dataset_id, schema_hash))""",
    """CREATE TABLE lineage_edges (
        upstream_id TEXT NOT NULL REFERENCES datasets(dataset_id),
        downstream_id TEXT NOT NULL REFERENCES datasets(dataset_id), via TEXT NOT NULL,
        revision TEXT NOT NULL, observed_at TEXT NOT NULL,
        PRIMARY KEY (upstream_id, downstream_id, via))""",
    """CREATE TABLE contracts (
        dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id), contract_path TEXT NOT NULL,
        contract_hash TEXT NOT NULL, contract_json TEXT NOT NULL, previous_hash TEXT,
        changed_at TEXT, last_checked_at TEXT NOT NULL, PRIMARY KEY (dataset_id, contract_path))""",
    """CREATE TABLE scan_sources (
        source_path TEXT PRIMARY KEY, kind TEXT NOT NULL, content_hash TEXT NOT NULL,
        scanned_at TEXT NOT NULL)""",
)
# v2: remember which spec file declared each edge, so a deleted spec's edges go too.
_V2 = ("ALTER TABLE lineage_edges ADD COLUMN spec_path TEXT",)

_FTS = (
    "CREATE VIRTUAL TABLE datasets_fts USING fts5("
    "dataset_id UNINDEXED, name, description, tags, columns, tokenize='unicode61')"
)


@dataclass(frozen=True)
class Dataset:
    dataset_id: str
    name: str
    uri: str
    format: str
    producer: str | None
    owner: str | None
    status: str
    deprecation_reason: str | None
    description: str | None
    last_updated_at: str | None
    tags: dict[str, str]


@dataclass(frozen=True)
class SchemaVersion:
    schema_hash: str
    snapshot: str
    origin: str
    first_seen_at: str
    last_seen_at: str


@dataclass(frozen=True)
class Edge:
    upstream_id: str
    downstream_id: str
    via: str


@dataclass(frozen=True)
class Contract:
    path: str
    contract_hash: str
    contract: dict[str, Any]
    previous_hash: str | None
    changed_at: str | None


@dataclass
class ScanReport:
    datasets: int = 0
    new_datasets: list[str] = field(default_factory=lambda: [])
    new_schema_versions: list[str] = field(default_factory=lambda: [])
    contract_changes: list[str] = field(default_factory=lambda: [])
    removed_edges: int = 0
    removed_contracts: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=lambda: [])


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class CatalogStore:
    def __init__(self, path: Path, *, clock: Callable[[], str] = _utcnow) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._now = clock
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        self.has_fts = bool(
            self._conn.execute("SELECT 1 FROM sqlite_master WHERE name='datasets_fts'").fetchone()
        )

    def _migrate(self) -> None:
        version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"aquila.db version {version} is newer than this Aquila ({SCHEMA_VERSION})"
            )
        if version < 1:
            with self._conn:
                for statement in _V1:
                    self._conn.execute(statement)
                # SQLite built without FTS5: search falls back to LIKE
                with contextlib.suppress(sqlite3.OperationalError):
                    self._conn.execute(_FTS)
                self._conn.execute("PRAGMA user_version=1")
        if version < 2:
            with self._conn:
                for statement in _V2:
                    self._conn.execute(statement)
                self._conn.execute("PRAGMA user_version=2")

        if version < 3:
            with self._conn:
                self._conn.execute(
                    "CREATE TABLE publications (dataset_id TEXT PRIMARY KEY "
                    "REFERENCES datasets(dataset_id), "
                    "source_path TEXT NOT NULL, snapshot TEXT NOT NULL, scanned_at TEXT NOT NULL)"
                )
                self._conn.execute("PRAGMA user_version=3")
        if version < 4:
            # v4: footer null counts, kept out of schema_versions so data never mints a version
            with self._conn:
                self._conn.execute(
                    "CREATE TABLE null_counts (dataset_id TEXT PRIMARY KEY "
                    "REFERENCES datasets(dataset_id), "
                    "counts_json TEXT NOT NULL, observed_at TEXT NOT NULL)"
                )
                self._conn.execute("PRAGMA user_version=4")

    def publications(self, nodes: Iterable[str]) -> dict[str, dict[str, Any]]:
        wanted = set(nodes)
        return {
            ident: dict(json.loads(snapshot), scanned_at=scanned_at)
            for ident, snapshot, scanned_at in self._conn.execute(
                "SELECT dataset_id, snapshot, scanned_at FROM publications"
            )
            if ident in wanted
        }

    def null_counts(self, ident: str) -> dict[str, int]:
        """Null count per column from the newest footer; a column absent here is unknown."""

        row = self._conn.execute(
            "SELECT counts_json FROM null_counts WHERE dataset_id=?", (ident,)
        ).fetchone()
        return {str(k): int(v) for k, v in json.loads(row[0]).items()} if row else {}

    def close(self) -> None:
        self._conn.close()

    # --- scan merge --------------------------------------------------------------------

    def apply(self, scan: ScanResult) -> ScanReport:
        report = ScanReport(skipped=list(scan.skipped))
        now = self._now()
        with self._conn:
            for observation in sorted(scan.datasets.values(), key=lambda d: d.uri):
                if self._upsert_dataset(
                    observation.uri, observation.format, observation.producer, now
                ):
                    report.new_datasets.append(observation.uri)
            observed: set[tuple[str, str, str]] = set()
            for edge in scan.edges:
                key = (dataset_id(edge.upstream), dataset_id(edge.downstream), edge.via)
                observed.add(key)
                self._conn.execute(
                    "INSERT INTO lineage_edges "
                    "(upstream_id, downstream_id, via, revision, observed_at, spec_path) "
                    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO UPDATE SET "
                    "revision=excluded.revision, observed_at=excluded.observed_at, "
                    "spec_path=excluded.spec_path",
                    (*key, edge.revision, now, edge.spec_path or None),
                )
            report.removed_edges = self._drop_stale_edges(scan.vias, observed)
            removed_edges, removed_contracts = self._drop_deleted_specs(scan.roots, scan.spec_paths)
            report.removed_edges += removed_edges
            report.removed_contracts = removed_contracts
            for uri, publication in scan.publications.items():
                self._conn.execute(
                    "INSERT OR REPLACE INTO publications VALUES (?,?,?,?)",
                    (dataset_id(uri), publication["source_path"], json.dumps(publication), now),
                )
            for schema in scan.schemas:
                if self._upsert_schema(
                    dataset_id(schema.uri), schema.snapshot, schema.origin, schema.observed_at
                ):
                    report.new_schema_versions.append(schema.uri)
            for nulls in scan.null_counts:
                self._conn.execute(
                    "INSERT OR REPLACE INTO null_counts VALUES (?,?,?)",
                    (dataset_id(nulls.uri), json.dumps(nulls.counts), nulls.observed_at),
                )
            for contract in scan.contracts:
                if self._upsert_contract(
                    dataset_id(contract.uri),
                    contract.path,
                    contract.contract_hash,
                    contract.contract,
                    now,
                ):
                    report.contract_changes.append(contract.uri)
            for fresh in scan.freshness:
                self._conn.execute(
                    "UPDATE datasets SET last_updated_at=?, freshness_origin=? WHERE dataset_id=? "
                    "AND (last_updated_at IS NULL OR last_updated_at < ?)",
                    (
                        fresh.last_updated_at,
                        fresh.origin,
                        dataset_id(fresh.uri),
                        fresh.last_updated_at,
                    ),
                )
            for source in scan.sources:
                self._conn.execute(
                    "INSERT INTO scan_sources VALUES (?, ?, ?, ?) ON CONFLICT DO UPDATE SET "
                    "kind=excluded.kind, content_hash=excluded.content_hash, "
                    "scanned_at=excluded.scanned_at",
                    (source.path, source.kind, source.content_hash, now),
                )
            for uri in scan.datasets:
                self._refresh_fts(dataset_id(uri))
        report.datasets = len(scan.datasets)
        return report

    def _free_name(self, base: str, ident: str) -> str:
        row = self._conn.execute("SELECT dataset_id FROM datasets WHERE name=?", (base,)).fetchone()
        return base if row is None or row[0] == ident else f"{base}~{ident[:6]}"

    def _upsert_dataset(self, uri: str, fmt: str, producer: str | None, now: str) -> bool:
        ident = dataset_id(uri)
        row = self._conn.execute(
            "SELECT producer FROM datasets WHERE dataset_id=?", (ident,)
        ).fetchone()
        base = producer.split(":", 1)[1] if producer else default_name(uri)
        if row is None:
            self._conn.execute(
                "INSERT INTO datasets (dataset_id, name, uri, format, producer, created_at, "
                "updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ident, self._free_name(base, ident), uri, fmt, producer, now, now),
            )
            return True
        if producer is not None and row[0] != producer:
            self._conn.execute(
                "UPDATE datasets SET producer=?, format=?, name=?, updated_at=? WHERE dataset_id=?",
                (producer, fmt, self._free_name(base, ident), now, ident),
            )
        return False

    def _drop_stale_edges(self, vias: set[str], observed: set[tuple[str, str, str]]) -> int:
        removed = 0
        for via in vias:
            for up, down in self._conn.execute(
                "SELECT upstream_id, downstream_id FROM lineage_edges WHERE via=?", (via,)
            ).fetchall():
                if (up, down, via) not in observed:
                    self._conn.execute(
                        "DELETE FROM lineage_edges WHERE upstream_id=? AND downstream_id=? AND "
                        "via=?",
                        (up, down, via),
                    )
                    removed += 1
        return removed

    def _drop_deleted_specs(self, roots: list[str], seen: set[str]) -> tuple[int, int]:
        """Edges and contracts declared by spec files that are gone from a scanned root."""

        def gone(spec: str | None) -> bool:
            if not spec or spec in seen:
                return False
            return any(Path(spec).is_relative_to(root) for root in roots)

        edges = [
            (up, down, via)
            for up, down, via, spec in self._conn.execute(
                "SELECT upstream_id, downstream_id, via, spec_path FROM lineage_edges"
            ).fetchall()
            if gone(spec)
        ]
        for edge in edges:
            self._conn.execute(
                "DELETE FROM lineage_edges WHERE upstream_id=? AND downstream_id=? AND via=?", edge
            )
        contracts = [
            (ident, path)
            for ident, path in self._conn.execute(
                "SELECT dataset_id, contract_path FROM contracts"
            ).fetchall()
            if gone(path)
        ]
        for contract in contracts:
            self._conn.execute(
                "DELETE FROM contracts WHERE dataset_id=? AND contract_path=?", contract
            )
        for ident, path in self._conn.execute(
            "SELECT dataset_id, source_path FROM publications"
        ).fetchall():
            if gone(path):
                self._conn.execute("DELETE FROM publications WHERE dataset_id=?", (ident,))
        return len(edges), len(contracts)

    def _upsert_schema(self, ident: str, snapshot: str, origin: str, seen: str) -> bool:
        digest = schema_hash(snapshot)
        row = self._conn.execute(
            "SELECT first_seen_at, last_seen_at FROM schema_versions WHERE dataset_id=? AND "
            "schema_hash=?",
            (ident, digest),
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO schema_versions VALUES (?, ?, ?, ?, ?, ?)",
                (ident, digest, snapshot, origin, seen, seen),
            )
            return True
        self._conn.execute(
            "UPDATE schema_versions SET first_seen_at=min(first_seen_at, ?), "
            "last_seen_at=max(last_seen_at, ?) WHERE dataset_id=? AND schema_hash=?",
            (seen, seen, ident, digest),
        )
        return False

    def _upsert_contract(
        self, ident: str, path: str, digest: str, body: dict[str, Any], now: str
    ) -> bool:
        row = self._conn.execute(
            "SELECT contract_hash FROM contracts WHERE dataset_id=? AND contract_path=?",
            (ident, path),
        ).fetchone()
        text = json.dumps(body, sort_keys=True)
        if row is None:
            self._conn.execute(
                "INSERT INTO contracts VALUES (?, ?, ?, ?, NULL, NULL, ?)",
                (ident, path, digest, text, now),
            )
            return False
        if row[0] == digest:
            self._conn.execute(
                "UPDATE contracts SET last_checked_at=? WHERE dataset_id=? AND contract_path=?",
                (now, ident, path),
            )
            return False
        self._conn.execute(
            "UPDATE contracts SET previous_hash=contract_hash, contract_hash=?, contract_json=?, "
            "changed_at=?, last_checked_at=? WHERE dataset_id=? AND contract_path=?",
            (digest, text, now, now, ident, path),
        )
        return True

    def _refresh_fts(self, ident: str) -> None:
        if not self.has_fts:
            return
        dataset = self.get(ident)
        if dataset is None:
            return
        latest = self.latest_schema(ident)
        columns = " ".join(f["name"] for f in json.loads(latest.snapshot)) if latest else ""
        tags = " ".join(f"{k} {v} {k}={v}" for k, v in dataset.tags.items())
        self._conn.execute("DELETE FROM datasets_fts WHERE dataset_id=?", (ident,))
        self._conn.execute(
            "INSERT INTO datasets_fts VALUES (?, ?, ?, ?, ?)",
            (ident, dataset.name, dataset.description or "", tags, columns),
        )

    # --- governance -------------------------------------------------------------------

    def set_tags(self, ident: str, tags: dict[str, str]) -> None:
        with self._conn:
            for key, value in tags.items():
                if key == "owner":
                    self._conn.execute(
                        "UPDATE datasets SET owner=? WHERE dataset_id=?", (value, ident)
                    )
                self._conn.execute(
                    "INSERT INTO dataset_tags VALUES (?, ?, ?) ON CONFLICT DO UPDATE SET "
                    "value=excluded.value",
                    (ident, key, value),
                )
            self._refresh_fts(ident)

    def set_description(self, ident: str, description: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE datasets SET description=? WHERE dataset_id=?", (description, ident)
            )
            self._refresh_fts(ident)

    def deprecate(self, ident: str, reason: str | None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE datasets SET status='deprecated', deprecation_reason=?, updated_at=? "
                "WHERE dataset_id=?",
                (reason, self._now(), ident),
            )

    # --- reads ------------------------------------------------------------------------

    def resolve(self, ref: str) -> Dataset | None:
        """By name, id, or any URI spelling of the same location."""

        for column, value in (("name", ref), ("dataset_id", ref), ("dataset_id", dataset_id(ref))):
            row = self._conn.execute(
                f"SELECT dataset_id FROM datasets WHERE {column}=?", (value,)
            ).fetchone()
            if row is not None:
                return self.get(str(row[0]))
        return None

    def get(self, ident: str) -> Dataset | None:
        row = self._conn.execute(
            "SELECT dataset_id, name, uri, format, producer, owner, status, deprecation_reason, "
            "description, last_updated_at FROM datasets WHERE dataset_id=?",
            (ident,),
        ).fetchone()
        if row is None:
            return None
        tags = dict(
            self._conn.execute(
                "SELECT key, value FROM dataset_tags WHERE dataset_id=? ORDER BY key", (ident,)
            ).fetchall()
        )
        return Dataset(*row, tags=tags)

    def datasets(self) -> list[Dataset]:
        idents = [r[0] for r in self._conn.execute("SELECT dataset_id FROM datasets ORDER BY name")]
        return [d for d in (self.get(i) for i in idents) if d is not None]

    def schema_versions(self, ident: str) -> list[SchemaVersion]:
        rows = self._conn.execute(
            "SELECT schema_hash, snapshot_json, origin, first_seen_at, last_seen_at FROM "
            "schema_versions "
            "WHERE dataset_id=? ORDER BY first_seen_at, schema_hash",
            (ident,),
        ).fetchall()
        return [SchemaVersion(*r) for r in rows]

    def latest_schema(self, ident: str) -> SchemaVersion | None:
        versions = self.schema_versions(ident)
        return max(versions, key=lambda v: (v.last_seen_at, v.first_seen_at)) if versions else None

    def edges(self) -> list[Edge]:
        rows = self._conn.execute(
            "SELECT upstream_id, downstream_id, via FROM lineage_edges ORDER BY via, upstream_id, "
            "downstream_id"
        ).fetchall()
        return [Edge(*r) for r in rows]

    def contracts(self, ident: str) -> list[Contract]:
        rows = self._conn.execute(
            "SELECT contract_path, contract_hash, contract_json, previous_hash, changed_at FROM "
            "contracts "
            "WHERE dataset_id=? ORDER BY contract_path",
            (ident,),
        ).fetchall()
        return [Contract(r[0], r[1], json.loads(r[2]), r[3], r[4]) for r in rows]

    def search(self, query: str) -> list[Dataset]:
        terms = [t for t in query.replace('"', " ").split() if t]
        if not terms:
            return []
        if self.has_fts:
            match = " AND ".join(f'"{t}"' for t in terms)
            rows = self._conn.execute(
                "SELECT dataset_id FROM datasets_fts WHERE datasets_fts MATCH ? ORDER BY rank",
                (match,),
            ).fetchall()
        else:  # pragma: no cover - CPython ships FTS5 on every CI platform
            rows = self._like(terms)
        return [d for d in (self.get(str(r[0])) for r in rows) if d is not None]

    def _like(self, terms: Iterable[str]) -> list[Any]:
        clause = " AND ".join("(name LIKE ? OR coalesce(description,'') LIKE ?)" for _ in terms)
        args = [a for t in terms for a in (f"%{t}%", f"%{t}%")]
        return self._conn.execute(
            f"SELECT dataset_id FROM datasets WHERE {clause}", args
        ).fetchall()

    def counts(self) -> dict[str, int]:
        def one(sql: str) -> int:
            return int(self._conn.execute(sql).fetchone()[0])

        return {
            "datasets": one("SELECT count(*) FROM datasets"),
            "deprecated": one("SELECT count(*) FROM datasets WHERE status='deprecated'"),
            "edges": one("SELECT count(*) FROM lineage_edges"),
            "schema_versions": one("SELECT count(*) FROM schema_versions"),
            "contracts": one("SELECT count(*) FROM contracts"),
        }
