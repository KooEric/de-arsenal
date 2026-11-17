"""Opt-in schema readers for datasets that are not local files.

``aquila scan`` stays offline by default. With ``--remote`` it reads the footer
of remote Parquet datasets (S3, GCS, Azure) through pyarrow's filesystems, using
the ambient credentials of the environment. With ``--iceberg-uri`` it loads
Iceberg tables from a REST catalog (``pyiceberg``, installed separately) and
records **every** schema the table has had, dated by the first snapshot that
used it.

Failures never stop a scan: each one becomes a ``skipped`` entry that names the
error class only, because provider messages can carry signed URLs or tokens.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from aquila.model import ScanResult, SchemaObservation, schema_snapshot

REMOTE_PARQUET_SCHEMES = {"s3", "gs", "gcs", "abfs", "abfss"}

# Loaded dynamically: pyarrow's filesystem stubs are not exported for type checkers.
pafs: Any = importlib.import_module("pyarrow.fs")
pq: Any = importlib.import_module("pyarrow.parquet")

FileSystemOpener = Callable[[str], tuple[Any, str]]
TableLoader = Callable[[str], Any]


def _open_filesystem(uri: str) -> tuple[Any, str]:
    if uri.startswith("gcs://"):
        uri = "gs://" + uri.removeprefix("gcs://")
    return cast(tuple[Any, str], pafs.FileSystem.from_uri(uri))


def _fields(arrow_schema: Any) -> list[dict[str, Any]]:
    return [
        {"name": str(f.name), "type": str(f.type), "nullable": bool(f.nullable)}
        for f in list(arrow_schema)
    ]


def remote_parquet_schema(
    uri: str, result: ScanResult, *, open_filesystem: FileSystemOpener | None = None
) -> None:
    """Footer schema of the last ``.parquet`` object (by key) under a remote prefix."""

    try:
        filesystem, path = (open_filesystem or _open_filesystem)(uri)
        infos: list[Any] = filesystem.get_file_info(pafs.FileSelector(path, recursive=True))
        files = sorted(
            (info for info in infos if info.type == pafs.FileType.File),
            key=lambda info: str(info.path),
        )
        files = [
            info
            for info in files
            if str(info.path).endswith(".parquet")
            and not str(info.path).rsplit("/", 1)[-1].startswith(".")
        ]
        if not files:
            return
        newest = files[-1]
        with filesystem.open_input_file(newest.path) as handle:
            schema: Any = pq.read_schema(handle)
        mtime = newest.mtime
        observed = (
            mtime.astimezone(UTC).isoformat()
            if isinstance(mtime, datetime)
            else datetime.now(UTC).isoformat()
        )
    except Exception as exc:  # noqa: BLE001 — provider errors are classified, not echoed
        result.skipped.append((uri, f"remote parquet unreadable ({type(exc).__name__})"))
        return
    result.schemas.append(
        SchemaObservation(uri, schema_snapshot(_fields(schema)), observed, "remote-footer")
    )


def iceberg_loader(catalog_uri: str, *, token_env: str | None = None) -> TableLoader:
    """A REST-catalog table loader; ``pyiceberg`` is imported only when asked for."""

    try:
        catalogs: Any = importlib.import_module("pyiceberg.catalog")
    except ImportError as exc:
        raise RuntimeError(
            "--iceberg-uri needs pyiceberg: pip install 'de-aquila[iceberg]'"
        ) from exc
    properties: dict[str, str] = {"type": "rest", "uri": catalog_uri}
    if token_env:
        token = os.environ.get(token_env)
        if not token:
            raise RuntimeError(f"environment variable {token_env!r} is not set (iceberg token)")
        properties["token"] = token
    catalog: Any = catalogs.load_catalog("aquila", **properties)
    return cast(TableLoader, catalog.load_table)


def iceberg_identifier(uri: str) -> str:
    return uri.removeprefix("iceberg://").strip("/").replace("/", ".")


def iceberg_schemas(uri: str, result: ScanResult, *, load_table: TableLoader) -> None:
    """Every schema of an Iceberg table, dated by the first snapshot that used it."""

    try:
        table: Any = load_table(iceberg_identifier(uri))
        first_seen: dict[int, int] = {}
        for snapshot in sorted(table.snapshots(), key=lambda s: int(s.timestamp_ms)):
            schema_id = snapshot.schema_id
            if schema_id is not None:
                first_seen.setdefault(int(schema_id), int(snapshot.timestamp_ms))
        current = int(table.schema().schema_id)
        observations: list[SchemaObservation] = []
        for schema_id, schema in sorted(cast(dict[int, Any], table.schemas()).items()):
            if schema_id in first_seen:
                stamp = datetime.fromtimestamp(first_seen[schema_id] / 1000, UTC).isoformat()
            elif schema_id == current:
                stamp = datetime.now(UTC).isoformat()  # an empty table: no snapshot yet
            else:
                continue  # a schema no snapshot ever used
            snapshot = schema_snapshot(_fields(schema.as_arrow()))
            observations.append(SchemaObservation(uri, snapshot, stamp, "iceberg-catalog"))
    except Exception as exc:  # noqa: BLE001
        result.skipped.append((uri, f"iceberg table unreadable ({type(exc).__name__})"))
        return
    result.schemas.extend(observations)
