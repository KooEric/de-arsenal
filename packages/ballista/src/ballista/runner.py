"""Fenced Ballista run ledger and crash-safe remote lifecycle orchestration."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any

from arsenal_core.errors import FatalError
from arsenal_core.execution import (
    ArtifactRef,
    ExecutionRequest,
    GuaranteeRequest,
    JobHandle,
    JobStatus,
    LeaseToken,
    RunContext,
)
from arsenal_core.state import RunRecord, StateStore
from ballista.artifacts import ArtifactError, ArtifactRepository, RunManifest
from ballista.executors.base import RemoteExecutor
from ballista.executors.spark_kubernetes import handle_from_mapping, handle_to_mapping

FaultHook = Callable[[str], None]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class BallistaRunner:
    """Coordinates one durable logical run; workers never open the SQLite ledger."""

    def __init__(
        self,
        *,
        state: StateStore,
        executor: RemoteExecutor,
        artifacts: ArtifactRepository,
        owner_id: str | None = None,
        lease_ttl_seconds: float = 300,
        clock: Callable[[], datetime] = _utcnow,
        fault_hook: FaultHook | None = None,
    ) -> None:
        if lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be positive")
        if executor.artifacts is not artifacts:
            raise ValueError("runner and executor must share one artifact repository")
        self._state = state
        self._executor = executor
        self._artifacts = artifacts
        self._owner_id = owner_id or f"ballista-{uuid.uuid4()}"
        self._lease_ttl_seconds = lease_ttl_seconds
        self._clock = clock
        self._fault_hook = fault_hook

    def submit(
        self,
        *,
        pipeline: str,
        revision: str,
        plan: Mapping[str, Any],
        inputs: tuple[ArtifactRef, ...],
        output: ArtifactRef,
        guarantees: GuaranteeRequest | None = None,
        run_id: str | None = None,
    ) -> RunRecord:
        """Preflight, persist an immutable manifest, then submit exactly one attempt."""

        logical_run_id = run_id or str(uuid.uuid4())
        if self._state.run(logical_run_id) is not None:
            raise FatalError(f"run {logical_run_id!r} already exists; use reconcile")
        lease = self._acquire(logical_run_id)
        try:
            now = self._now()
            request = ExecutionRequest(
                context=RunContext(
                    run_id=logical_run_id,
                    pipeline=pipeline,
                    revision=revision,
                    lease=lease,
                    started_at=now,
                ),
                plan=dict(plan),
                inputs=inputs,
                output=output,
                guarantees=guarantees
                or GuaranteeRequest(result_exactly_once=True, atomic_publish=True),
            )
            self._executor.preflight(request)
            manifest = RunManifest(request=request, attempt=0, created_at=now)
            manifest_uri = self._artifacts.write_manifest(manifest)
            self._fault("after_manifest")
            self._state.create_run(
                run_id=logical_run_id,
                pipeline=pipeline,
                revision=revision,
                mode="transform",
                executor=self._executor.name,
                guarantees=asdict(request.guarantees),
            )
            self._fault("after_run_planned")
            try:
                handle = self._executor.submit_manifest(manifest, manifest_uri)
            except Exception as exc:
                self._state.update_run(
                    logical_run_id,
                    status=JobStatus.FAILED.value,
                    lease=lease,
                    error=_safe_error(exc),
                )
                raise
            self._fault("after_submit")
            self._state.update_run(
                logical_run_id,
                status=JobStatus.SUBMITTED.value,
                lease=lease,
                external_job=handle_to_mapping(handle),
            )
            self._fault("after_handle_recorded")
            return self._require_run(logical_run_id)
        finally:
            self._state.release_lease(lease)

    def status(self, run_id: str) -> RunRecord:
        """Read only the durable coordinator view; use reconcile for remote effects."""

        return self._require_run(run_id)

    def reconcile(self, run_id: str) -> RunRecord:
        """Recover a known job/proof, or resubmit only after deterministic absence."""

        lease = self._acquire(run_id)
        try:
            record = self._require_run(run_id)
            manifest = self._artifacts.read_manifest(run_id, record.attempt)
            try:
                verified = self._artifacts.read_output_proof(manifest)
            except ArtifactError as exc:
                self._state.update_run(
                    run_id,
                    status=JobStatus.LOST.value,
                    lease=lease,
                    external_job=record.external_job,
                    error=_safe_error(exc),
                )
                return self._require_run(run_id)
            if verified is not None:
                self._state.update_run(
                    run_id,
                    status=JobStatus.SUCCEEDED.value,
                    lease=lease,
                    external_job=record.external_job,
                )
                return self._require_run(run_id)
            if record.status in {
                JobStatus.SUCCEEDED.value,
                JobStatus.FAILED.value,
                JobStatus.CANCELLED.value,
            }:
                return record

            known_handle = _record_handle(record)
            observation = self._executor.reconcile(run_id, known_handle)
            if observation.handle is not None:
                return self._record_observation(
                    run_id, record, lease, observation.handle, observation.status
                )
            if observation.status is not JobStatus.LOST:
                raise FatalError("executor reconcile returned a status without a job handle")

            # Once a driver reported success, absence of its proof is ambiguous:
            # the transaction may be visible even though the proof write failed.
            # Never auto-retry that uncertainty over a potentially committed result.
            if record.error and "reports success" in record.error:
                self._state.update_run(
                    run_id,
                    status=JobStatus.LOST.value,
                    lease=lease,
                    external_job=record.external_job,
                    error="remote job is absent and no immutable output proof exists",
                )
                return self._require_run(run_id)

            if record.status == JobStatus.PLANNED.value and known_handle is None:
                # Crash after manifest/ledger but before submit: reuse the exact
                # immutable attempt manifest, as promised by the recovery contract.
                return self._submit_planned(record, manifest, lease)
            return self._plan_retry(record, manifest, lease)
        finally:
            self._state.release_lease(lease)

    def cancel(self, run_id: str) -> RunRecord:
        """Cancel the known/recovered remote job and fence the ledger transition."""

        lease = self._acquire(run_id)
        try:
            record = self._require_run(run_id)
            if record.status == JobStatus.SUCCEEDED.value:
                raise FatalError("a succeeded immutable result cannot be cancelled")
            if record.status in {JobStatus.CANCELLED.value, JobStatus.FAILED.value}:
                return record
            handle = _record_handle(record)
            if handle is None:
                observation = self._executor.reconcile(run_id)
                handle = observation.handle
            if handle is not None:
                self._executor.cancel(handle)
            self._state.update_run(
                run_id,
                status=JobStatus.CANCELLED.value,
                lease=lease,
                external_job=handle_to_mapping(handle) if handle is not None else None,
            )
            return self._require_run(run_id)
        finally:
            self._state.release_lease(lease)

    def _record_observation(
        self,
        run_id: str,
        record: RunRecord,
        lease: LeaseToken,
        handle: JobHandle,
        status: JobStatus,
    ) -> RunRecord:
        if status is JobStatus.SUCCEEDED:
            # The worker status alone is insufficient. Reconciliation will check
            # the proof again on the next pass, so record uncertainty explicitly.
            next_status = JobStatus.LOST
            error = "remote job reports success but immutable output proof is missing"
        else:
            next_status = status
            error = None
        self._state.update_run(
            run_id,
            status=next_status.value,
            lease=lease,
            external_job=handle_to_mapping(handle),
            error=error,
        )
        return self._require_run(run_id)

    def _plan_retry(self, record: RunRecord, prior: RunManifest, lease: LeaseToken) -> RunRecord:
        attempt = record.attempt + 1
        context = replace(prior.request.context, lease=lease)
        request = replace(prior.request, context=context)
        self._executor.preflight(request)
        manifest = RunManifest(request=request, attempt=attempt, created_at=self._now())
        manifest_uri = self._artifacts.write_manifest(manifest)
        self._state.update_run(
            record.run_id,
            status=JobStatus.PLANNED.value,
            lease=lease,
            increment_attempt=True,
        )
        self._fault("after_retry_planned")
        planned = self._require_run(record.run_id)
        return self._submit_planned(planned, manifest, lease, manifest_uri=manifest_uri)

    def _submit_planned(
        self,
        record: RunRecord,
        manifest: RunManifest,
        lease: LeaseToken,
        *,
        manifest_uri: str | None = None,
    ) -> RunRecord:
        self._executor.preflight(manifest.request)
        uri = manifest_uri or self._artifacts.manifest_uri(record.run_id, record.attempt)
        try:
            handle = self._executor.submit_manifest(manifest, uri)
        except Exception as exc:
            self._state.update_run(
                record.run_id,
                status=JobStatus.FAILED.value,
                lease=lease,
                error=_safe_error(exc),
            )
            raise
        self._fault("after_reconcile_submit")
        self._state.update_run(
            record.run_id,
            status=JobStatus.SUBMITTED.value,
            lease=lease,
            external_job=handle_to_mapping(handle),
        )
        return self._require_run(record.run_id)

    def _acquire(self, run_id: str) -> LeaseToken:
        lease = self._state.acquire_lease(f"run:{run_id}", self._owner_id, self._lease_ttl_seconds)
        if lease is None:
            raise FatalError(f"run is already leased: {run_id}")
        return lease

    def _require_run(self, run_id: str) -> RunRecord:
        record = self._state.run(run_id)
        if record is None:
            raise FatalError(f"unknown Ballista run: {run_id}")
        if record.executor != self._executor.name:
            raise FatalError(
                f"run {run_id!r} belongs to executor {record.executor!r}, "
                f"not {self._executor.name!r}"
            )
        return record

    def _fault(self, stage: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(stage)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("BallistaRunner clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def _record_handle(record: RunRecord) -> JobHandle | None:
    if record.external_job is None:
        return None
    return handle_from_mapping(record.external_job)


def _safe_error(exc: Exception) -> str:
    # Profile and subprocess errors are already redacted. Keep the ledger bounded
    # and avoid serializing repr(), tracebacks, or arbitrary exception attributes.
    message = str(exc).replace("\n", " ")
    return f"{type(exc).__name__}: {message[:500]}"


__all__ = ["BallistaRunner", "FaultHook"]
