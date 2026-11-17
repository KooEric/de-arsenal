"""Portable execution contracts, without engine or storage dependencies."""

from arsenal_core.execution.artifacts import ArtifactRef, OutputProof
from arsenal_core.execution.canonical import (
    canonical_hash,
    canonical_json,
    canonical_value,
    spec_revision,
)
from arsenal_core.execution.capabilities import (
    ExecutorCapabilities,
    GuaranteeRequest,
    SinkCapabilities,
)
from arsenal_core.execution.models import (
    ExecutionRequest,
    JobHandle,
    JobStatus,
    LeaseToken,
    RunContext,
)
from arsenal_core.execution.protocol import Executor

__all__ = [
    "ArtifactRef",
    "ExecutionRequest",
    "Executor",
    "ExecutorCapabilities",
    "GuaranteeRequest",
    "JobHandle",
    "JobStatus",
    "LeaseToken",
    "OutputProof",
    "RunContext",
    "SinkCapabilities",
    "canonical_hash",
    "canonical_json",
    "canonical_value",
    "spec_revision",
]
