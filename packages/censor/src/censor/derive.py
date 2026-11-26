"""Which data does a project produce? Read from the specs, so nothing has to be passed."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from censor.context import ProjectContext, SpecFile
from censor.target import DataTarget, DuckDBTarget, FilesTarget


@dataclass(frozen=True)
class Derived:
    label: str  # the spec file the target comes from
    target: DataTarget | None
    key: tuple[str, ...] = ()
    reason: str = ""  # why there is no target


def _mapping(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _local(root: Path, path: object) -> Path | None:
    if not isinstance(path, str) or "://" in path:
        return None
    return Path(path) if Path(path).is_absolute() else root / path


def _parquet_glob(path: Path) -> FilesTarget:
    return FilesTarget(str(path) if path.is_file() else str(path / "**" / "*.parquet"))


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(v for v in cast(list[object], value) if isinstance(v, str))


def _unique_field(raw: dict[str, Any]) -> tuple[str, ...]:
    rules: object = _mapping(raw.get("validate")).get("rules")
    for rule in cast(list[object], rules) if isinstance(rules, list) else []:
        entry = _mapping(rule)
        field: object = entry.get("field")
        if entry.get("unique") and isinstance(field, str):
            return (field,)
    return ()


def _pipeline(root: Path, spec: SpecFile) -> Derived:
    raw = spec.raw or {}
    sink = _mapping(raw.get("sink"))
    kind: object = sink.get("type")
    path = _local(root, sink.get("path"))
    if kind == "parquet" and path is not None:
        return Derived(spec.name, _parquet_glob(path), _unique_field(raw))
    table: object = sink.get("table")
    if kind == "duckdb" and path is not None and isinstance(table, str):
        return Derived(spec.name, DuckDBTarget(path, table), _strings(sink.get("merge_key")))
    return Derived(spec.name, None, reason=f"the {kind} sink is not a local file or DuckDB table")


def _dedup_key(raw: dict[str, Any]) -> tuple[str, ...]:
    steps: object = raw.get("steps")
    key: tuple[str, ...] = ()
    for step in cast(list[object], steps) if isinstance(steps, list) else []:
        columns = _strings(_mapping(step).get("dedup"))
        if columns:
            key = columns
    return key


def _transform(root: Path, spec: SpecFile) -> Derived:
    raw = spec.raw or {}
    path = _local(root, raw.get("output"))
    if path is None:
        return Derived(spec.name, None, reason="the output is not a local path")
    return Derived(spec.name, _parquet_glob(path), _dedup_key(raw))


def derive_targets(project: ProjectContext) -> list[Derived]:
    """One target per pipeline sink and per transform output."""
    pipelines = [_pipeline(project.root, s) for s in project.pipelines if s.raw is not None]
    transforms = [_transform(project.root, s) for s in project.transform_files if s.raw is not None]
    return [*pipelines, *transforms]
