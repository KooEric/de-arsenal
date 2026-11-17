"""Who writes into a dataset, and how maintenance takes it over safely.

A writer (a Hasta stream, or a Pugio run with a local Parquet sink) registers
itself next to the dataset it writes. Maintenance that rewrites the directory
(Onager compaction) must then take each writer's lease for the duration:

* a live runner holds the lease → maintenance refuses instead of racing it
  (a batch landing mid-swap would be deleted with the backup);
* a runner that starts during maintenance cannot get the lease and stops;
* uncommitted batches would be replayed under the same identity after their
  files were merged away, so they also block maintenance.

Registrations are small JSON files in ``.<dataset>.writers/`` beside the
dataset, so this works without any shared service.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from arsenal_core.errors import FatalError
from arsenal_core.execution import canonical_hash
from arsenal_core.state import StateStore


class DatasetBusyError(FatalError):
    """A registered writer is active or has uncommitted output in the dataset."""


@dataclass(frozen=True)
class StreamWriter:
    state_db: str
    resource: str
    pipeline: str
    stream_name: str


def _registry(dataset: Path) -> Path:
    resolved = dataset.resolve()
    return resolved.parent / f".{resolved.name}.writers"


def register_stream_writer(dataset: Path, writer: StreamWriter) -> Path:
    """Idempotently record ``writer`` as a producer of ``dataset``."""

    registry = _registry(dataset)
    registry.mkdir(parents=True, exist_ok=True)
    path = registry / f"{canonical_hash(asdict(writer))[:16]}.json"
    if not path.exists():
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(writer), sort_keys=True), encoding="utf-8")
        temp.replace(path)
    return path


def stream_writers(dataset: Path) -> list[StreamWriter]:
    registry = _registry(dataset)
    if not registry.is_dir():
        return []
    writers: list[StreamWriter] = []
    for path in sorted(registry.glob("*.json")):
        try:
            writers.append(StreamWriter(**json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError) as exc:
            raise FatalError(f"unreadable writer registration {path}: {exc}") from exc
    return writers


@contextmanager
def exclusive_dataset(
    dataset: Path, *, owner_id: str, ttl_seconds: float = 1800
) -> Generator[list[StreamWriter], None, None]:
    """Hold every registered writer's lease while the caller rewrites ``dataset``."""

    writers = [w for w in stream_writers(dataset) if Path(w.state_db).exists()]
    with ExitStack() as stack:
        for writer in writers:
            store = StateStore(Path(writer.state_db))
            stack.callback(store.close)
            lease = store.acquire_lease(writer.resource, owner_id, ttl_seconds)
            if lease is None:
                holder = store.lease(writer.resource)
                who = holder.owner_id if holder is not None else "another process"
                raise DatasetBusyError(
                    f"{dataset} is being written by {writer.resource} "
                    f"(lease held by {who}); wait for it or stop it first"
                )
            stack.callback(store.release_lease, lease)
            pending = store.stream_batch_summary(writer.pipeline, writer.stream_name).by_status
            if pending.get("planned", 0):
                raise DatasetBusyError(
                    f"stream {writer.pipeline}/{writer.stream_name} has "
                    f"{pending['planned']} uncommitted batch(es) in {dataset}; rewriting now "
                    "would duplicate them on replay. Run the stream to catch up first"
                )
        yield writers


__all__ = [
    "DatasetBusyError",
    "StreamWriter",
    "exclusive_dataset",
    "register_stream_writer",
    "stream_writers",
]
