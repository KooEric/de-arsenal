"""Schema snapshot comparison primitives shared by collection components."""

import json
from dataclasses import dataclass
from typing import Any, cast


@dataclass(frozen=True)
class SchemaChange:
    """One field-level schema change."""

    field: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None


@dataclass(frozen=True)
class SchemaDiff:
    """Added, removed, and modified fields between two snapshots."""

    added: tuple[SchemaChange, ...]
    removed: tuple[SchemaChange, ...]
    changed: tuple[SchemaChange, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def summary(self) -> str:
        """Return a compact, stable message suitable for logs and state."""
        parts: list[str] = []
        if self.added:
            parts.append("added=" + ",".join(change.field for change in self.added))
        if self.removed:
            parts.append("removed=" + ",".join(change.field for change in self.removed))
        if self.changed:
            parts.append("changed=" + ",".join(change.field for change in self.changed))
        return "; ".join(parts)


def compare_schema_snapshots(previous: str, current: str) -> SchemaDiff:
    """Compare snapshots produced by the runner's deterministic JSON encoder.

    Snapshot entries are intentionally treated as opaque dictionaries except for
    their ``name`` key. This lets future snapshot metadata be added without
    changing the comparison algorithm, while still detecting type/nullability
    changes in today's format.
    """
    previous_fields = _fields(previous)
    current_fields = _fields(current)
    added: list[SchemaChange] = []
    removed: list[SchemaChange] = []
    changed: list[SchemaChange] = []

    for name in sorted(current_fields.keys() - previous_fields.keys()):
        added.append(SchemaChange(name, None, current_fields[name]))
    for name in sorted(previous_fields.keys() - current_fields.keys()):
        removed.append(SchemaChange(name, previous_fields[name], None))
    for name in sorted(current_fields.keys() & previous_fields.keys()):
        before = previous_fields[name]
        after = current_fields[name]
        if before != after:
            changed.append(SchemaChange(name, before, after))

    return SchemaDiff(tuple(added), tuple(removed), tuple(changed))


def _fields(snapshot: str) -> dict[str, dict[str, Any]]:
    raw = cast(object, json.loads(snapshot))
    if not isinstance(raw, list):
        raise ValueError("schema snapshot must be a JSON list")
    entries = cast(list[object], raw)
    fields: dict[str, dict[str, Any]] = {}
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("schema snapshot entries must contain a string name")
        entry = cast(dict[str, Any], raw_entry)
        if not isinstance(entry.get("name"), str):
            raise ValueError("schema snapshot entries must contain a string name")
        fields[entry["name"]] = entry
    return fields
