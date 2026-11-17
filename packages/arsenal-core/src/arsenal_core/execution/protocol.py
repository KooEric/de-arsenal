"""Protocols that execution engines implement without importing their SDKs here."""

from __future__ import annotations

from typing import Protocol

from arsenal_core.execution.capabilities import ExecutorCapabilities
from arsenal_core.execution.models import ExecutionRequest, JobHandle, JobStatus


class Executor(Protocol):
    name: str
    capabilities: ExecutorCapabilities

    def preflight(self, request: ExecutionRequest) -> None: ...

    def submit(self, request: ExecutionRequest) -> JobHandle: ...

    def status(self, handle: JobHandle) -> JobStatus: ...

    def cancel(self, handle: JobHandle) -> None: ...
