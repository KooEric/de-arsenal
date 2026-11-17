"""Typed stream-domain values and deterministic microbatch identities."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

import pyarrow as pa

from arsenal_core.execution import OutputProof, canonical_hash, canonical_value


@dataclass(frozen=True)
class StreamPosition:
    """Opaque, source-owned checkpoint position represented as canonical JSON data."""

    value: Mapping[str, Any]

    def canonical(self) -> Mapping[str, Any]:
        return canonical_value(self.value)

    @property
    def sequence(self) -> int | None:
        """Return a CDC sequence checkpoint, rejecting malformed positions.

        REST cursors deliberately do not expose a sequence, so ``None`` is a
        valid result when the key is absent.  A present non-integer value is a
        corrupted checkpoint and must fail before it changes an outbox query.
        """

        value = self.value.get("sequence")
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("outbox checkpoint sequence must be an integer")
        return value


@dataclass(frozen=True)
class BatchLimits:
    max_records: int
    max_bytes: int = 64 * 1024 * 1024
    max_wait_seconds: float = 20


@dataclass(frozen=True)
class CommittedBatch:
    """The sink-facing identity of a microbatch, before its checkpoint is committed."""

    batch_id: str
    pipeline: str
    stream_name: str
    revision: str
    source_id: str
    start: StreamPosition
    end: StreamPosition
    event_hash: str


class StreamingSource(Protocol):
    source_id: str

    def poll(self, after: StreamPosition | None, limits: BatchLimits) -> MicroBatch: ...

    def close(self) -> None: ...


@runtime_checkable
class StreamingSink(Protocol):
    """A sink that treats ``batch_id`` as an idempotency key."""

    def write_batch(self, batch: CommittedBatch, table: pa.Table) -> OutputProof: ...


def batch_identity(
    *,
    pipeline: str,
    stream_name: str,
    revision: str,
    source_id: str,
    start: StreamPosition,
    end: StreamPosition,
    event_hash: str,
    generation: int = 0,
) -> str:
    """Return the stable SHA-256 identity required for retry-safe sink writes.

    ``generation`` changes only on an explicit checkpoint reset, so a replay
    produces new batches instead of being skipped as already committed.
    Generation 0 is left out of the hash to keep pre-reset identities stable.
    """

    payload: dict[str, object] = {
        "pipeline": pipeline,
        "stream_name": stream_name,
        "revision": revision,
        "source_id": source_id,
        "start_position": start.canonical(),
        "end_position": end.canonical(),
        "event_hash": event_hash,
    }
    if generation:
        payload["generation"] = generation
    return canonical_hash(payload)


@dataclass(frozen=True)
class CdcEvent:
    """Immutable v1 CDC envelope emitted by an ordered outbox.

    ``event_id`` identifies one source event, while ``sequence`` is the
    source-local checkpoint position.  They intentionally have different
    purposes: the sequence orders polling and the id allows an apply sink to
    make retries a no-op.
    """

    event_id: str
    sequence: int
    op: Literal["I", "U", "D"]
    key: Mapping[str, Any]
    occurred_at: datetime
    payload: Mapping[str, Any] | None = None
    schema_version: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("CDC event_id must not be empty")
        if type(self.sequence) is not int:
            raise ValueError("CDC sequence must be an integer")
        if self.sequence < 0:
            raise ValueError("CDC sequence must be non-negative")
        if self.op not in {"I", "U", "D"}:
            raise ValueError("CDC op must be I, U, or D")
        if not self.key:
            raise ValueError("CDC key must not be empty")
        if any(not name for name in self.key):
            raise ValueError("CDC key names must be non-empty strings")
        if any(value is None for value in self.key.values()):
            raise ValueError("CDC key values must not be null")
        if self.occurred_at.tzinfo is None:
            raise ValueError("CDC occurred_at must be timezone-aware")
        if self.op in {"I", "U"} and self.payload is None:
            raise ValueError("CDC I/U events require a full payload")
        if self.op == "D" and self.payload is not None:
            raise ValueError("CDC D events must not include a payload")

    def canonical(self) -> Mapping[str, Any]:
        """Stable immutable representation used for batch and ledger hashes."""
        return canonical_value(
            {
                "event_id": self.event_id,
                "sequence": self.sequence,
                "op": self.op,
                "key": self.key,
                "occurred_at": self.occurred_at,
                "payload": self.payload,
                "schema_version": self.schema_version,
            }
        )


def cdc_event_hash(events: Sequence[CdcEvent]) -> str:
    """Hash the ordered immutable envelopes in a CDC microbatch."""

    return canonical_hash([event.canonical() for event in events])


@dataclass(frozen=True)
class MicroBatch:
    start: StreamPosition
    end: StreamPosition
    events: pa.Table
    event_hash: str
    source_exhausted: bool
    changes: tuple[CdcEvent, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.events, pa.Table):
            raise TypeError("microbatch events must be a PyArrow table")
        if self.changes is not None:
            expected = cdc_event_hash(self.changes)
            if not self.event_hash:
                object.__setattr__(self, "event_hash", expected)
            elif self.event_hash != expected:
                raise ValueError("CDC microbatch event_hash does not match its immutable envelopes")


CDCEvent = CdcEvent
ChangeEvent = CdcEvent
"""Compatibility name for the normalized CDC event envelope."""
