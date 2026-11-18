"""YAML loader for a dispatch spec.

Unlike the other loaders, `${ENV_VAR}` is NOT substituted here. The spec keeps
the placeholder and the destination resolves it per request, so a token never
reaches the spec object, the ledger or `pilum status` output.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from arsenal_core.errors import FatalError
from arsenal_core.yaml_io import yaml_error_summary
from pilum.spec import DispatchSpec


def load_dispatch(path: Path) -> DispatchSpec:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise FatalError(f"dispatch spec file not found: {path}") from error
    except OSError as error:
        raise FatalError(f"cannot read dispatch spec {path}: {error}") from error
    except yaml.YAMLError as error:
        raise FatalError(f"invalid YAML in {path}: {yaml_error_summary(error)}") from error
    try:
        return DispatchSpec.model_validate(raw)
    except ValidationError as error:
        lines = [
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        ]
        raise FatalError(f"invalid dispatch spec {path}:\n  " + "\n  ".join(lines)) from error
