"""Read the last committed dataset publication and its latest attempt in one snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aquila.model import EdgeObservation, FreshnessObservation, ScanResult
from aquila.uri import dataset_id, normalize_uri


def scan_publication(path: Path, result: ScanResult) -> None:
    # An unreadable (e.g. writer-locked) source still exists. Retain its last observed lineage.
    result.spec_paths.add(str(path))
    try:
        import duckdb
    except ImportError:
        result.skipped.append(
            (str(path), "install duckdb in the Aquila environment to scan publications")
        )
        return

    try:
        with duckdb.connect(
            str(path), read_only=True, config={"enable_external_access": "false"}
        ) as db:
            db.execute("BEGIN")
            tables = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
            row = (
                db.execute("SELECT report FROM runs ORDER BY id DESC LIMIT 1").fetchone()
                if "runs" in tables
                else None
            )
            failure = (
                db.execute(
                    "SELECT message, failed_at AT TIME ZONE 'UTC' FROM failure LIMIT 1"
                ).fetchone()
                if "failure" in tables
                else None
            )
            report: dict[str, Any] = json.loads(row[0]) if row else {}
            rules = report.get("rules", {})
            files = sorted(report.get("fingerprints", {}))
            success = report.get("last_success")
            data = report.get("result", {})
            source = normalize_uri(rules["files"], root=path.parent.parent) if success else None
            attempt = {
                "status": "failed" if failure else "succeeded",
                "at": failure[1].isoformat() + "+00:00" if failure else success,
                "error": failure[0] if failure else None,
            }
    except (duckdb.Error, KeyError, ValueError, TypeError, AttributeError) as exc:
        result.skipped.append((str(path), f"unreadable dataset publication: {type(exc).__name__}"))
        return
    if not success and not failure:
        return
    uri = f"duckdb://{path.as_posix()}/published"
    via = f"dataset:apply-{dataset_id(uri)}"
    result.dataset(uri, "duckdb")
    result.vias.add(via)
    if source and success:
        result.dataset(source, "files")
        result.edges.append(EdgeObservation(source, uri, via, report["version"], str(path)))
        result.freshness.append(FreshnessObservation(uri, success, "dataset-apply"))
    result.publications[uri] = {
        "source_path": str(path),
        "files": files,
        "rules": rules,
        "published_at": success,
        "version": report.get("version"),
        "rows": data.get("rows"),
        "period_start": data.get("first"),
        "period_end": data.get("last"),
        "latest_attempt": attempt,
    }
