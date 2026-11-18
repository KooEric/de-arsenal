"""Read declarations and observations without importing (or migrating) other tools.

Specs are parsed as plain YAML, so ``de-aquila`` does not depend on Pugio,
Gladius, Hasta or Ballista. State databases are opened read-only through
sqlite: opening them with ``StateStore`` would migrate and back them up, and
only their owning tool may write them (P2-ADR-7).
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq
import yaml

from aquila.model import (
    ContractObservation,
    EdgeObservation,
    FreshnessObservation,
    ScanResult,
    SchemaObservation,
    SourceFile,
    schema_snapshot,
)
from aquila.uri import normalize_uri
from arsenal_core.execution import canonical_hash

_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "backups"}


def iter_spec_files(paths: list[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_file():
            yield path
            continue
        for candidate in sorted(path.rglob("*")):
            if candidate.suffix not in {".yaml", ".yml"} or not candidate.is_file():
                continue
            relative = candidate.relative_to(path).parts[:-1]
            if any(part in _SKIP_DIRS or part.startswith(".") for part in relative):
                continue
            yield candidate


def classify(raw: dict[str, Any]) -> str | None:
    if isinstance(raw.get("pipelines"), list) or isinstance(raw.get("transforms"), list):
        return "arsenal"
    if isinstance(raw.get("source"), dict) and isinstance(raw.get("sink"), dict):
        return "hasta" if isinstance(raw.get("stream"), dict) else "pugio"
    if isinstance(raw.get("input"), str) and isinstance(raw.get("output"), str):
        return "gladius"
    return None


def load_yaml(path: Path) -> dict[str, Any] | None:
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else None


def distributed_transforms(project_file: Path, raw: dict[str, Any]) -> set[Path]:
    """Transforms an ``arsenal.yaml`` sends to Ballista (they are recorded as ``ballista:``)."""

    found: set[Path] = set()
    for entry in cast(list[object], raw.get("distributed") or []):
        if isinstance(entry, dict):
            transform = cast(dict[str, Any], entry).get("transform")
            if isinstance(transform, str):
                found.add((project_file.parent / transform).resolve())
    return found


def _source_uri(source: dict[str, Any], name: str, root: Path) -> tuple[str, str] | None:
    kind = source.get("type")
    if kind in {"rest", "rest_poll"} and isinstance(source.get("url"), str):
        return normalize_uri(source["url"]), "rest"
    if kind == "file" and isinstance(source.get("path"), str):
        return normalize_uri(source["path"], root=root), str(source.get("format", "file"))
    if kind in {"database", "database_outbox", "database_table_cursor"}:
        dialect = str(source.get("dialect", "postgres"))
        uri = f"{dialect}://${{{source.get('dsn_env', '')}}}/{source.get('table', '')}"
        return normalize_uri(uri), dialect
    if kind in {"python", "dlt"} and isinstance(source.get("target"), str):
        return f"{kind}://{source['target']}", kind
    return None


def sink_uri(sink: dict[str, Any], root: Path) -> tuple[str, str] | None:
    kind = sink.get("type")
    if kind == "parquet" and isinstance(sink.get("path"), str):
        return normalize_uri(sink["path"], root=root), "parquet"
    if kind == "duckdb" and isinstance(sink.get("path"), str):
        database = normalize_uri(sink["path"], root=root).removeprefix("file://")
        return f"duckdb://{database}/{sink.get('table', '')}", "duckdb"
    if kind == "postgres":
        return normalize_uri(
            f"postgres://${{{sink.get('dsn_env', '')}}}/{sink.get('table', '')}"
        ), "postgres"
    if kind == "iceberg" and isinstance(sink.get("table"), str):
        return f"iceberg://{sink['table']}", "iceberg"
    if kind == "redshift":
        return normalize_uri(
            f"redshift://${{{sink.get('dsn_env', '')}}}/{sink.get('table', '')}"
        ), "redshift"
    return None


def _format_of(uri: str) -> str:
    scheme = uri.split("://", 1)[0]
    if scheme in {"iceberg", "duckdb", "postgres", "postgresql", "redshift"}:
        return scheme
    return "parquet"


def scan_spec(path: Path, raw: dict[str, Any], kind: str, root: Path, result: ScanResult) -> None:
    name = str(raw.get("name") or path.stem)
    via = f"{kind}:{name}"
    revision = canonical_hash(raw)
    spec_path = str(path.resolve())
    result.vias.add(via)
    result.spec_paths.add(spec_path)
    if kind == "gladius" or kind == "ballista":
        upstream = normalize_uri(raw["input"], root=root)
        downstream = normalize_uri(raw["output"], root=root)
        result.dataset(upstream, _format_of(upstream))
        result.dataset(downstream, _format_of(downstream), producer=via)
        result.edges.append(EdgeObservation(upstream, downstream, via, revision, spec_path))
        return
    sink = sink_uri(cast(dict[str, Any], raw["sink"]), root)
    if sink is None:
        result.skipped.append((str(path), f"unsupported sink type {raw['sink'].get('type')!r}"))
        return
    result.dataset(sink[0], sink[1], producer=via)
    source = _source_uri(cast(dict[str, Any], raw["source"]), name, root)
    if source is not None:
        result.dataset(source[0], source[1])
        result.edges.append(EdgeObservation(source[0], sink[0], via, revision, spec_path))
    contract = raw.get("contract")
    if isinstance(contract, dict):
        result.contracts.append(
            ContractObservation(sink[0], spec_path, cast(dict[str, Any], contract))
        )
    state = root / str(raw.get("state_dir", ".arsenal")) / f"{name}.db"
    if state.is_file():
        read_state(state, kind, name, sink[0], result)


def _readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def read_state(path: Path, kind: str, name: str, sink: str, result: ScanResult) -> None:
    try:
        con = _readonly(path)
    except sqlite3.Error as exc:
        result.skipped.append((str(path), f"cannot open state db read-only: {exc}"))
        return
    try:
        if kind == "pugio":
            for captured_at, snapshot in con.execute(
                "SELECT captured_at, schema_json FROM schema_snapshots WHERE pipeline=? "
                "ORDER BY captured_at",
                (name,),
            ):
                result.schemas.append(
                    SchemaObservation(sink, str(snapshot), str(captured_at), "pugio-state")
                )
            row = con.execute(
                "SELECT max(updated_at) FROM units WHERE pipeline=? AND status='done'", (name,)
            ).fetchone()
            if row and row[0]:
                result.freshness.append(FreshnessObservation(sink, str(row[0]), "pugio-state"))
        else:
            row = con.execute(
                "SELECT max(updated_at) FROM stream_checkpoints WHERE pipeline=?", (name,)
            ).fetchone()
            if row and row[0]:
                result.freshness.append(FreshnessObservation(sink, str(row[0]), "hasta-checkpoint"))
    except sqlite3.Error as exc:
        result.skipped.append((str(path), f"unreadable state db: {exc}"))
    finally:
        con.close()
    result.sources.append(SourceFile(str(path), "state", file_hash(path)))


def parquet_schema(uri: str, result: ScanResult) -> None:
    """Record the footer schema of a local Parquet dataset (its last file, by name)."""

    if not uri.startswith("file://"):
        return
    location = Path(uri.removeprefix("file://"))
    files = (
        [location]
        if location.is_file()
        else sorted(location.rglob("*.parquet"))
        if location.is_dir()
        else []
    )
    files = [f for f in files if f.is_file() and not f.name.startswith(".")]
    if not files:
        return
    newest = files[-1]
    try:
        schema: list[Any] = list(pq.read_schema(newest))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    except (OSError, ValueError) as exc:
        result.skipped.append((str(newest), f"unreadable parquet footer: {exc}"))
        return
    fields: list[dict[str, Any]] = [
        {"name": str(f.name), "type": str(f.type), "nullable": bool(f.nullable)} for f in schema
    ]
    snapshot = schema_snapshot(fields)
    observed = datetime.fromtimestamp(newest.stat().st_mtime, UTC).isoformat()
    result.schemas.append(SchemaObservation(uri, snapshot, observed, "parquet-footer"))


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()
