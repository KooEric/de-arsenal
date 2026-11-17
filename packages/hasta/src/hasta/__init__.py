"""Hasta — leased REST microbatch streaming with durable checkpoints."""

from importlib.metadata import PackageNotFoundError, version

from hasta.model import (
    BatchLimits,
    CDCEvent,
    CdcEvent,
    ChangeEvent,
    CommittedBatch,
    MicroBatch,
    StreamPosition,
    batch_identity,
    cdc_event_hash,
)
from hasta.runner import CdcRunner, StreamRunner
from hasta.spec import DatabaseOutboxSourceSpec, StreamSpec, TableCursorSourceSpec

try:
    __version__ = version("de-hasta")
except PackageNotFoundError:
    __version__ = "0.5.1"

__all__ = [
    "BatchLimits",
    "CDCEvent",
    "ChangeEvent",
    "CdcEvent",
    "CdcRunner",
    "CommittedBatch",
    "MicroBatch",
    "StreamPosition",
    "StreamRunner",
    "StreamSpec",
    "DatabaseOutboxSourceSpec",
    "TableCursorSourceSpec",
    "batch_identity",
    "cdc_event_hash",
]
