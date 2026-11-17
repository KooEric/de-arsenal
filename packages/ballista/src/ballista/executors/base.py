"""Remote executor lifecycle extension over the engine-neutral core protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from arsenal_core.execution import (
    ExecutionRequest,
    ExecutorCapabilities,
    JobHandle,
    JobStatus,
)
from ballista.artifacts import ArtifactRepository, RunManifest
from ballista.preflight import PreflightReport


@dataclass(frozen=True)
class ReconcileObservation:
    handle: JobHandle | None
    status: JobStatus


class RemoteExecutor(Protocol):
    name: str
    capabilities: ExecutorCapabilities
    artifacts: ArtifactRepository

    def preflight_report(self, request: ExecutionRequest) -> PreflightReport: ...

    def preflight(self, request: ExecutionRequest) -> None: ...

    def submit(self, request: ExecutionRequest) -> JobHandle: ...

    def submit_manifest(self, manifest: RunManifest, manifest_uri: str) -> JobHandle: ...

    def status(self, handle: JobHandle) -> JobStatus: ...

    def cancel(self, handle: JobHandle) -> None: ...

    def reconcile(
        self, run_id: str, known_handle: JobHandle | None = None
    ) -> ReconcileObservation: ...


__all__ = ["ReconcileObservation", "RemoteExecutor"]
