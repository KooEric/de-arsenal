"""Run rules over `events` and append new alerts to a Parquet directory.

An alert is identified by (rule, event). Running again over the same lake finds the same
ids, sees they are already on disk, and writes nothing.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from vigil.rule import Rule

ALERT_SCHEMA: Final = pa.schema(
    [
        ("alert_id", pa.string()),
        ("rule_id", pa.string()),
        ("severity", pa.string()),
        ("attack", pa.list_(pa.string())),
        ("event_id", pa.string()),
        ("event_time", pa.timestamp("us")),
        ("actor_id", pa.string()),
        ("detected_at", pa.timestamp("us")),
    ]
)

_ALERT_ID_LENGTH: Final = 32


@dataclass(frozen=True)
class RunSummary:
    new_by_rule: dict[str, int]
    failed: dict[str, str]
    new_severities: frozenset[str]


def alert_id(rule_id: str, event_id: str) -> str:
    digest = hashlib.sha256(f"{rule_id}\x1f{event_id}".encode()).hexdigest()
    return digest[:_ALERT_ID_LENGTH]


def existing_alert_ids(out_dir: Path) -> set[str]:
    ids: set[str] = set()
    if not out_dir.is_dir():
        return ids
    for file in sorted(out_dir.glob("*.parquet")):
        ids.update(pq.read_table(file, columns=["alert_id"]).column("alert_id").to_pylist())  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
    return ids


def run_rules(
    rules: list[Rule], con: duckdb.DuckDBPyConnection, out_dir: Path, detected_at: datetime
) -> RunSummary:
    seen = existing_alert_ids(out_dir)
    rows: list[dict[str, Any]] = []
    new_by_rule: dict[str, int] = {}
    failed: dict[str, str] = {}
    for rule in rules:
        try:
            hits = con.execute(
                "SELECT DISTINCT e.event_id, e.event_time, e.actor_id "
                f"FROM ({rule.query}\n) AS q JOIN events AS e ON e.event_id = q.event_id "
                "ORDER BY e.event_time, e.event_id"
            ).fetchall()
        except duckdb.Error as e:
            failed[rule.id] = str(e)
            continue
        count = 0
        for event_id, event_time, actor_id in hits:
            identifier = alert_id(rule.id, str(event_id))
            if identifier in seen:
                continue
            seen.add(identifier)
            count += 1
            rows.append(
                {
                    "alert_id": identifier,
                    "rule_id": rule.id,
                    "severity": rule.severity,
                    "attack": rule.attack,
                    "event_id": event_id,
                    "event_time": event_time,
                    "actor_id": actor_id,
                    "detected_at": detected_at,
                }
            )
        new_by_rule[rule.id] = count
    if rows:
        _write(out_dir, rows, detected_at)
    return RunSummary(new_by_rule, failed, frozenset(str(row["severity"]) for row in rows))


def _write(out_dir: Path, rows: list[dict[str, Any]], detected_at: datetime) -> None:
    """Write to a temp name and rename, so a crash leaves the existing alerts untouched."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = detected_at.strftime("%Y%m%dT%H%M%S")
    final = out_dir / f"alerts-{stamp}-{uuid4().hex[:8]}.parquet"
    temp = out_dir / f".{final.name}.tmp"
    try:
        pq.write_table(pa.Table.from_pylist(rows, schema=ALERT_SCHEMA), temp)  # pyright: ignore[reportUnknownMemberType]
        os.replace(temp, final)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
