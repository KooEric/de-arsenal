"""Load a DE Arsenal project for auditing without running or changing anything."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from arsenal.project import ArsenalProject, DbtProjectRef, load_project

from arsenal_core.errors import ArsenalError, FatalError

MANIFEST_NAME = "arsenal.yaml"


@dataclass(frozen=True)
class SpecFile:
    path: Path
    root: Path
    text: str
    raw: dict[str, Any] | None
    read_error: str | None = None

    @property
    def name(self) -> str:
        try:
            return str(self.path.relative_to(self.root))
        except ValueError:
            return str(self.path)


@dataclass(frozen=True)
class ProjectContext:
    root: Path
    manifest: ArsenalProject | None
    manifest_error: str | None
    pipelines: tuple[SpecFile, ...]
    transforms: tuple[Path | DbtProjectRef, ...]
    transform_files: tuple[SpecFile, ...]


def _reason(error: Exception) -> str:
    if isinstance(error, OSError) and error.strerror:
        return error.strerror
    return str(error) or type(error).__name__


def _read_spec(path: Path, root: Path) -> SpecFile:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return SpecFile(path, root, "", None, read_error=_reason(e))
    try:
        loaded: object = yaml.safe_load(text)
    except yaml.YAMLError:
        return SpecFile(path, root, text, None)
    raw = cast(dict[str, Any], loaded) if isinstance(loaded, dict) else None
    return SpecFile(path, root, text, raw)


def load_context(root: Path) -> ProjectContext:
    root = root.absolute()
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FatalError(f"no {MANIFEST_NAME} in {root}; pass a DE Arsenal project directory")
    try:
        manifest = load_project(manifest_path)
    except ArsenalError as e:
        return ProjectContext(root, None, str(e), (), (), ())
    pipelines = tuple(_read_spec(p, root) for p in manifest.pipelines)
    transforms = tuple(manifest.transforms)
    transform_files = tuple(_read_spec(t, root) for t in transforms if isinstance(t, Path))
    return ProjectContext(root, manifest, None, pipelines, transforms, transform_files)
