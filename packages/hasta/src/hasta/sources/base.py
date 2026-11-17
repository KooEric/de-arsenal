from __future__ import annotations

from typing import Protocol

from hasta.model import BatchLimits, MicroBatch, StreamPosition


class StreamingSource(Protocol):
    source_id: str

    def poll(self, after: StreamPosition | None, limits: BatchLimits) -> MicroBatch: ...

    def close(self) -> None: ...
