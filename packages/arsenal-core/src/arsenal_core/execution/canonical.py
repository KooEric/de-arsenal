"""Deterministic JSON normalization and content hashing helpers."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from typing import Any, cast


def canonical_value(value: Any) -> Any:
    """Return a JSON-safe value with deterministic mapping ordering.

    The function deliberately rejects arbitrary objects rather than falling back to
    ``str``: identities must not change with a process-specific representation.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return canonical_value(dataclasses.asdict(value))
    if isinstance(value, Enum):
        return canonical_value(value.value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("canonical timestamps must be timezone-aware")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        mapping = cast(dict[object, object], value)
        return {
            str(key): canonical_value(item)
            for key, item in sorted(mapping.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    raise TypeError(f"cannot canonicalize {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize JSON without whitespace and with recursively sorted object keys."""
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    """SHA-256 of :func:`canonical_json`, expressed as lower-case hexadecimal."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def spec_revision(spec: Mapping[str, Any]) -> str:
    """The deterministic revision identifier for a declarative pipeline/plan."""
    return canonical_hash(spec)
