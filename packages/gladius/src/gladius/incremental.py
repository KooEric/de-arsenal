"""Persistent bookkeeping and file discovery for incremental transforms."""

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from gladius.spec import TransformSpec


@dataclass(frozen=True)
class InputFile:
    path: Path
    key: str
    signature: str


class IncrementalStore:
    """Small SQLite journal making incremental output restart-safe."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS input_files (
                path TEXT PRIMARY KEY,
                signature TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self._conn.commit()

    def spec_hash(self) -> str | None:
        row = self._conn.execute("SELECT value FROM metadata WHERE key='spec_hash'").fetchone()
        return str(row[0]) if row else None

    def reset(self, spec_hash: str) -> None:
        self._conn.execute("DELETE FROM input_files")
        self._conn.execute(
            "INSERT INTO metadata(key, value) VALUES ('spec_hash', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (spec_hash,),
        )
        self._conn.commit()

    def signatures(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT path, signature FROM input_files").fetchall()
        return {str(path): str(signature) for path, signature in rows}

    def mark_processed(self, files: list[InputFile]) -> None:
        self._conn.executemany(
            "INSERT INTO input_files(path, signature) VALUES (?, ?) "
            "ON CONFLICT(path) DO UPDATE SET signature=excluded.signature, "
            "updated_at=CURRENT_TIMESTAMP",
            [(item.key, item.signature) for item in files],
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def transform_hash(spec: TransformSpec) -> str:
    payload = spec.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def discover_input_files(spec: TransformSpec) -> list[InputFile]:
    root = Path(spec.input)
    output = Path(spec.output).absolute()
    candidates = [root] if root.is_file() else sorted(root.glob("**/*.parquet"))
    result: list[InputFile] = []
    for path in candidates:
        resolved = path.absolute()
        if resolved == output or output in resolved.parents:
            continue
        stat = path.stat()
        key = str(resolved)
        signature = f"{stat.st_size}:{stat.st_mtime_ns}"
        result.append(InputFile(path, key, signature))
    return result


def state_path(spec: TransformSpec) -> Path:
    return Path(spec.state_dir) / f"{spec.name}.db"


def reset_output(output: Path) -> None:
    if output.exists():
        shutil.rmtree(output)


def atomic_replace_directory(tmp: Path, output: Path) -> None:
    if output.exists():
        shutil.rmtree(output)
    os.replace(tmp, output)
