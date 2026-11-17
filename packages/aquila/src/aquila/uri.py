"""Dataset identity: the same location is the same dataset whichever tool names it.

``dataset_id = canonical_hash(normalize_uri(uri))[:16]``. Normalization rules
(pinned by tests):

- the scheme is lower-cased; a bare path becomes an absolute ``file://`` URI
  (relative paths resolve against the scan root, as ``arsenal run`` does)
- a trailing glob segment (``*.parquet``, ``**``, ``part-{a,b}``) and a trailing
  ``/`` are dropped, so ``out/`` and ``out/*.parquet`` are one dataset
- query strings, fragments and URL credentials are dropped — they carry cursors
  and secrets, not identity; http(s) hosts are lower-cased
- ``${VAR}`` placeholders are kept verbatim, never expanded
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from arsenal_core.execution import canonical_hash

_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_GLOB = re.compile(r"[*?\[{]")
_DATA_SUFFIXES = (".parquet", ".csv", ".jsonl", ".json", ".duckdb", ".db", ".xlsx")


def _strip_glob(path: str) -> str:
    parts = path.split("/")
    while len(parts) > 1 and _GLOB.search(parts[-1]):
        parts.pop()
    return "/".join(parts).rstrip("/") or "/"


def normalize_uri(uri: str, *, root: Path | None = None) -> str:
    text = uri.strip()
    match = _SCHEME.match(text)
    if match is None or _WINDOWS_DRIVE.match(text):
        base = root if root is not None else Path.cwd()
        local = Path(text)
        absolute = local if local.is_absolute() else base / local
        return "file://" + _strip_glob(str(PurePosixPath(absolute.as_posix())))
    scheme = match.group(1).lower()
    parts = urlsplit(text)
    netloc = parts.netloc.rsplit("@", 1)[-1]
    if scheme in {"http", "https"}:
        netloc = netloc.lower()
    path = _strip_glob(parts.path) if parts.path else ""
    return urlunsplit((scheme, netloc, "" if path == "/" else path, "", ""))


def dataset_id(uri: str, *, root: Path | None = None) -> str:
    return canonical_hash({"uri": normalize_uri(uri, root=root)})[:16]


def default_name(normalized: str) -> str:
    """A readable name: the last path segment without a data-file suffix, else the host."""

    rest = normalized.split("://", 1)[-1]
    netloc, _, path = rest.partition("/")
    tail = path.rstrip("/").rsplit("/", 1)[-1] if path else netloc
    for suffix in _DATA_SUFFIXES:
        if tail.endswith(suffix) and len(tail) > len(suffix):
            return tail[: -len(suffix)]
    return tail or netloc
