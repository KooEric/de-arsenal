"""Frozen, engine-neutral execution models.

These are intentionally dataclasses rather than spec models: they describe an
already validated request crossing an executor boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from arsenal_core.execution.capabilities import GuaranteeRequest

ArtifactFormat = Literal["parquet", "iceberg", "duckdb", "postgres", "redshift"]


@dataclass(frozen=True)
class ArtifactRef:
    uri: str
    format: ArtifactFormat
    version: str | None = None
    content_hash: str | None = None


@dataclass(frozen=True)
class LeaseToken:
    resource: str
    owner_id: str
    fence: int
    expires_at: datetime


@dataclass(frozen=True)
class RunContext:
    run_id: str
    pipeline: str
    revision: str
    lease: LeaseToken
    started_at: datetime


@dataclass(frozen=True)
class ExecutionRequest:
    context: RunContext
    plan: Mapping[str, Any]
    inputs: tuple[ArtifactRef, ...]
    output: ArtifactRef
    guarantees: GuaranteeRequest


class JobStatus(StrEnum):
    PLANNED = "planned"
    SUBMITTED = "submitted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LOST = "lost"


@dataclass(frozen=True)
class JobHandle:
    executor: str
    job_id: str
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class OutputProof:
    run_id: str
    revision: str
    output: ArtifactRef
    input_versions: tuple[str, ...]
    row_count: int | None
    schema_hash: str | None
    plan_hash: str
    started_at: datetime
    finished_at: datetime
    output_version: str | None = None
