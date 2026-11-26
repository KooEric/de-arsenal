"""Scan orchestration and the read models behind ``aquila show``/``history``."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scorpio import assess_freshness

from aquila.lineage import find_cycles
from aquila.model import ScanResult, SourceFile
from aquila.publications import scan_publication
from aquila.remote import (
    REMOTE_PARQUET_SCHEMES,
    TableLoader,
    iceberg_schemas,
    remote_parquet_schema,
)
from aquila.scanners import (
    classify,
    distributed_transforms,
    file_hash,
    iter_spec_files,
    load_yaml,
    parquet_schema,
    scan_spec,
)
from aquila.store import CatalogStore, Dataset, ScanReport
from arsenal_core.schema import compare_schema_snapshots

DEFAULT_DB = Path(".arsenal") / "aquila.db"


def collect(
    paths: list[Path],
    *,
    root: Path,
    remote: bool = False,
    iceberg: TableLoader | None = None,
) -> ScanResult:
    """Read every spec under ``paths``; nothing is written anywhere."""

    result = ScanResult(roots=[str(p.resolve()) for p in paths if p.is_dir()])
    files = list(iter_spec_files(paths))
    parsed: list[tuple[Path, dict[str, Any] | None]] = []
    for path in files:
        if path.name == "dataset.duckdb" and path.parent.name == ".arsenal":
            scan_publication(path.resolve(), result)
        else:
            parsed.append((path, load_yaml(path)))
    ballista: set[Path] = set()
    for path, raw in parsed:
        if raw is not None and classify(raw) == "arsenal":
            ballista |= distributed_transforms(path, raw)
    for path, raw in parsed:
        kind = classify(raw) if raw is not None else None
        if raw is None or kind is None:
            continue
        if kind == "arsenal":
            result.sources.append(SourceFile(str(path), kind, file_hash(path)))
            continue
        if kind == "gladius" and path.resolve() in ballista:
            kind = "ballista"
        scan_spec(path, raw, kind, root, result)
        result.sources.append(SourceFile(str(path), kind, file_hash(path)))
    for uri in sorted(result.datasets):
        fmt = result.datasets[uri].format
        scheme = uri.split("://", 1)[0]
        if fmt == "parquet" and scheme == "file":
            parquet_schema(uri, result)
        elif fmt == "parquet" and remote and scheme in REMOTE_PARQUET_SCHEMES:
            remote_parquet_schema(uri, result)
        elif fmt == "iceberg" and iceberg is not None:
            iceberg_schemas(uri, result, load_table=iceberg)
    return result


def scan(
    store: CatalogStore,
    paths: list[Path],
    *,
    root: Path,
    remote: bool = False,
    iceberg: TableLoader | None = None,
) -> ScanReport:
    return store.apply(collect(paths, root=root, remote=remote, iceberg=iceberg))


def schema_fields(snapshot: str) -> list[dict[str, Any]]:
    return list(json.loads(snapshot))


# Parquet has no second-precision timestamp: pyarrow writes timestamp[s] as timestamp[ms].
_TIMESTAMP = re.compile(r"timestamp\[(s|ms|us|ns)(.*)\]")


def type_satisfies(contract_type: str, stored_type: str, *, parquet: bool) -> bool:
    """Whether data stored as ``stored_type`` satisfies a contract's ``contract_type``."""

    if contract_type == stored_type:
        return True
    declared, stored = _TIMESTAMP.fullmatch(contract_type), _TIMESTAMP.fullmatch(stored_type)
    return (
        parquet
        and declared is not None
        and stored is not None
        and (declared[1], stored[1]) == ("s", "ms")
        and declared[2] == stored[2]
    )


def _null_finding(
    name: str, field: dict[str, Any], nulls: int | None, *, parquet: bool
) -> str | None:
    # Parquet footers mark every field nullable, so only the statistics say anything.
    if parquet:
        return f"{name}: {nulls} null value(s) in data, NOT NULL in contract" if nulls else None
    if field.get("nullable") is True:
        return f"{name}: nullable in data, NOT NULL in contract"
    return None


def contract_findings(store: CatalogStore, dataset: Dataset) -> list[str]:
    """Where the latest observed schema (and, for Parquet, its null counts) breaks a contract."""

    latest = store.latest_schema(dataset.dataset_id)
    if latest is None:
        return []
    actual = {f["name"]: f for f in schema_fields(latest.snapshot)}
    parquet = dataset.format == "parquet"
    nulls = store.null_counts(dataset.dataset_id) if parquet else {}
    findings: list[str] = []
    for contract in store.contracts(dataset.dataset_id):
        for column in contract.contract.get("columns", []):
            name = column.get("name")
            field = actual.get(name)
            if field is None:
                findings.append(f"{name}: missing (contract {contract.path})")
            elif "type" in column and not type_satisfies(
                str(column["type"]), str(field["type"]), parquet=parquet
            ):
                findings.append(f"{name}: type {field['type']} != contract {column['type']}")
            elif column.get("nullable") is False:
                finding = _null_finding(name, field, nulls.get(name), parquet=parquet)
                if finding is not None:
                    findings.append(finding)
        if not contract.contract.get("allow_extra", False):
            declared = {c.get("name") for c in contract.contract.get("columns", [])}
            for extra in sorted(set(actual) - declared):
                findings.append(f"{extra}: not in contract {contract.path}")
    return findings


def deprecated_upstreams(store: CatalogStore, ident: str) -> list[Dataset]:
    ups = {e.upstream_id for e in store.edges() if e.downstream_id == ident}
    return [
        d for d in (store.get(u) for u in sorted(ups)) if d is not None and d.status == "deprecated"
    ]


def deprecation_warnings(store: CatalogStore) -> list[str]:
    warnings: list[str] = []
    names = {d.dataset_id: d.name for d in store.datasets()}
    for edge in store.edges():
        upstream = store.get(edge.upstream_id)
        if upstream is not None and upstream.status == "deprecated":
            reason = f" ({upstream.deprecation_reason})" if upstream.deprecation_reason else ""
            reader = names.get(edge.downstream_id)
            warnings.append(f"{reader} reads deprecated {upstream.name}{reason} via {edge.via}")
    return warnings


def multiple_writer_warnings(store: CatalogStore, ident: str | None = None) -> list[str]:
    """Datasets that more than one spec writes — usually a copy-paste mistake."""

    writers: dict[str, set[str]] = {}
    for edge in store.edges():
        writers.setdefault(edge.downstream_id, set()).add(edge.via)
    names = {d.dataset_id: d.name for d in store.datasets()}
    return [
        f"{names.get(target, target)} is written by {len(vias)} specs: {', '.join(sorted(vias))}"
        for target, vias in sorted(writers.items(), key=lambda item: names.get(item[0], item[0]))
        if len(vias) > 1 and (ident is None or target == ident)
    ]


def cycle_names(store: CatalogStore) -> list[list[str]]:
    names = {d.dataset_id: d.name for d in store.datasets()}
    return [[names.get(n, n) for n in cycle] for cycle in find_cycles(store.edges())]


def show(
    store: CatalogStore, dataset: Dataset, *, max_age_seconds: float | None = None
) -> dict[str, Any]:
    names = {d.dataset_id: d.name for d in store.datasets()}
    edges = store.edges()
    latest = store.latest_schema(dataset.dataset_id)
    freshness: dict[str, Any] = {"last_updated_at": dataset.last_updated_at}
    if max_age_seconds is not None:
        assessed = assess_freshness(dataset.last_updated_at, max_age_seconds=max_age_seconds)
        freshness |= {
            "status": assessed.status,
            "age_seconds": assessed.age_seconds,
            "alert": assessed.alert,
        }
    return {
        "dataset": asdict(dataset),
        "schema": schema_fields(latest.snapshot) if latest else None,
        "schema_versions": len(store.schema_versions(dataset.dataset_id)),
        "contracts": [
            {"path": c.path, "hash": c.contract_hash, "changed_at": c.changed_at}
            for c in store.contracts(dataset.dataset_id)
        ],
        "contract_findings": contract_findings(store, dataset),
        "upstream": sorted(
            {
                f"{names.get(e.upstream_id)} ({e.via})"
                for e in edges
                if e.downstream_id == dataset.dataset_id
            }
        ),
        "downstream": sorted(
            {
                f"{names.get(e.downstream_id)} ({e.via})"
                for e in edges
                if e.upstream_id == dataset.dataset_id
            }
        ),
        "warnings": [
            f"reads deprecated {d.name}"
            + (f" ({d.deprecation_reason})" if d.deprecation_reason else "")
            for d in deprecated_upstreams(store, dataset.dataset_id)
        ]
        + multiple_writer_warnings(store, dataset.dataset_id),
        "freshness": freshness,
    }


def history(store: CatalogStore, dataset: Dataset) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    previous: str | None = None
    for version in store.schema_versions(dataset.dataset_id):
        entry: dict[str, Any] = {
            "schema_hash": version.schema_hash[:12],
            "first_seen_at": version.first_seen_at,
            "last_seen_at": version.last_seen_at,
            "origin": version.origin,
            "columns": len(schema_fields(version.snapshot)),
        }
        if previous is not None:
            diff = compare_schema_snapshots(previous, version.snapshot)
            entry["diff"] = {
                "added": [c.field for c in diff.added],
                "removed": [c.field for c in diff.removed],
                "changed": [
                    {
                        "field": c.field,
                        "before": (c.before or {}).get("type"),
                        "after": (c.after or {}).get("type"),
                    }
                    for c in diff.changed
                ],
            }
        timeline.append(entry)
        previous = version.snapshot
    return timeline
