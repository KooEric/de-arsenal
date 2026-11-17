"""Spark Kubernetes submission adapter with deterministic run resources.

The adapter owns lifecycle calls, not Spark data semantics or output commits. A
worker receives only an immutable manifest URI and must publish a verified
transaction/snapshot proof before the coordinator can mark the run successful.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.execution import (
    ExecutionRequest,
    ExecutorCapabilities,
    JobHandle,
    JobStatus,
    canonical_hash,
)
from ballista.artifacts import ArtifactRepository, RunManifest
from ballista.config import BallistaProfile
from ballista.executors.base import ReconcileObservation
from ballista.preflight import (
    EnvironmentProbe,
    PreflightError,
    PreflightReport,
    UnverifiedEnvironmentProbe,
    preflight_request,
)

_SUBMISSION_ID = re.compile(r"(?:submissionId|submission ID)\s*[:=]\s*([A-Za-z0-9_.:-]+)")
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?$")


@dataclass(frozen=True)
class SparkApplication:
    run_id: str
    attempt: int
    application_name: str
    driver_pod: str
    manifest_uri: str
    manifest_hash: str
    master: str
    namespace: str
    driver_image: str
    service_account: str
    worker_entrypoint: str
    labels: Mapping[str, str]
    spark_conf: Mapping[str, str | int | float | bool]
    executor_instances: int
    executor_cores: int
    executor_memory: str


@dataclass(frozen=True)
class SparkSubmission:
    job_id: str
    driver_pod: str
    status: JobStatus = JobStatus.SUBMITTED


class SparkKubernetesClient(Protocol):
    def submit(self, application: SparkApplication) -> SparkSubmission: ...

    def status(self, job_id: str, driver_pod: str) -> JobStatus: ...

    def cancel(self, job_id: str, driver_pod: str) -> None: ...

    def find(self, run_id: str, driver_pod: str) -> SparkSubmission | None: ...


class SparkKubernetesExecutor:
    name = "spark_kubernetes"
    capabilities = ExecutorCapabilities(
        result_exactly_once=True,
        atomic_publish=True,
        delete_apply=False,
        watermark=False,
        concurrent_writers=True,
        input_versioning=True,
    )

    def __init__(
        self,
        *,
        profile: BallistaProfile,
        artifacts: ArtifactRepository,
        client: SparkKubernetesClient,
        environment_probe: EnvironmentProbe | None = None,
    ) -> None:
        self.profile = profile
        self.artifacts = artifacts
        self.client = client
        self.environment_probe = environment_probe or UnverifiedEnvironmentProbe()

    def preflight_report(self, request: ExecutionRequest) -> PreflightReport:
        return preflight_request(
            request,
            profile=self.profile,
            executor_name=self.name,
            executor_capabilities=self.capabilities,
            environment=self.environment_probe.inspect(self.profile),
        )

    def preflight(self, request: ExecutionRequest) -> None:
        self.preflight_report(request).require()

    def submit(self, request: ExecutionRequest) -> JobHandle:
        """Core Executor compatibility path for an initial attempt."""

        manifest = RunManifest(request=request, attempt=0, created_at=request.context.started_at)
        manifest_uri = self.artifacts.write_manifest(manifest)
        return self.submit_manifest(manifest, manifest_uri)

    def submit_manifest(self, manifest: RunManifest, manifest_uri: str) -> JobHandle:
        self.preflight(manifest.request)
        expected_uri = self.artifacts.manifest_uri(
            manifest.request.context.run_id, manifest.attempt
        )
        if manifest_uri != expected_uri:
            raise FatalError("manifest URI does not match its deterministic run identity")
        application = self._application(manifest, manifest_uri)
        submission = self.client.submit(application)
        return JobHandle(
            executor=self.name,
            job_id=submission.job_id,
            metadata={
                "run_id": manifest.request.context.run_id,
                "attempt": manifest.attempt,
                "driver_pod": submission.driver_pod,
                "manifest_uri": manifest_uri,
                "manifest_hash": manifest.content_hash,
            },
        )

    def status(self, handle: JobHandle) -> JobStatus:
        self._validate_handle(handle)
        return self.client.status(handle.job_id, _driver_pod(handle))

    def cancel(self, handle: JobHandle) -> None:
        self._validate_handle(handle)
        self.client.cancel(handle.job_id, _driver_pod(handle))

    def reconcile(self, run_id: str, known_handle: JobHandle | None = None) -> ReconcileObservation:
        driver_pod = driver_resource_name(run_id)
        if known_handle is not None:
            self._validate_handle(known_handle)
            known_status = self.status(known_handle)
            if known_status is not JobStatus.LOST:
                return ReconcileObservation(known_handle, known_status)
            driver_pod = _driver_pod(known_handle)
        found = self.client.find(run_id, driver_pod)
        if found is None:
            return ReconcileObservation(None, JobStatus.LOST)
        handle = JobHandle(
            executor=self.name,
            job_id=found.job_id,
            metadata={"run_id": run_id, "driver_pod": found.driver_pod},
        )
        return ReconcileObservation(handle, found.status)

    def _application(self, manifest: RunManifest, manifest_uri: str) -> SparkApplication:
        run_id = manifest.request.context.run_id
        master = self.profile.master_url()
        if master is None:
            raise PreflightError(
                "Ballista preflight failed:\n"
                "- [profile.master] a k8s:// Spark master was not resolved"
            )
        driver_pod = driver_resource_name(run_id)
        labels = {
            "de-arsenal.io/run-id": run_label_value(run_id),
            "de-arsenal.io/attempt": str(manifest.attempt),
        }
        return SparkApplication(
            run_id=run_id,
            attempt=manifest.attempt,
            application_name=driver_pod,
            driver_pod=driver_pod,
            manifest_uri=manifest_uri,
            manifest_hash=manifest.content_hash,
            master=master,
            namespace=self.profile.cluster.namespace,
            driver_image=self.profile.cluster.driver_image,
            service_account=self.profile.cluster.service_account,
            worker_entrypoint="local:///opt/de-arsenal/ballista-worker.py",
            labels=labels,
            spark_conf=dict(self.profile.spark.conf),
            executor_instances=self.profile.spark.executor_instances,
            executor_cores=self.profile.spark.executor_cores,
            executor_memory=self.profile.spark.executor_memory,
        )

    def _validate_handle(self, handle: JobHandle) -> None:
        if handle.executor != self.name:
            raise ValueError(
                f"job handle belongs to {handle.executor!r}, expected executor {self.name!r}"
            )
        _driver_pod(handle)


class SubprocessSparkKubernetesClient:
    """Thin, shell-free spark-submit/kubectl lifecycle client.

    This class does not establish RBAC, storage, image, or commit guarantees. It
    must be paired with a real :class:`EnvironmentProbe`; the executor's default
    unverified probe prevents accidental production use.
    """

    def __init__(
        self,
        profile: BallistaProfile,
        *,
        spark_submit: str = "spark-submit",
        kubectl: str = "kubectl",
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.profile = profile
        self.spark_submit = spark_submit
        self.kubectl = kubectl
        self.environ = dict(os.environ if environ is None else environ)

    def submit(self, application: SparkApplication) -> SparkSubmission:
        command = self._submit_command(application)
        completed = self._run(command, "Spark submission")
        match = _SUBMISSION_ID.search(completed.stdout)
        job_id = match.group(1) if match is not None else application.driver_pod
        return SparkSubmission(job_id=job_id, driver_pod=application.driver_pod)

    def status(self, job_id: str, driver_pod: str) -> JobStatus:
        master = self.profile.master_url(self.environ)
        if master is None:
            raise FatalError("Spark master is not configured")
        completed = self._run(
            [self.spark_submit, "--status", job_id, "--master", master],
            "Spark status",
            allow_failure=True,
        )
        output = (completed.stdout + "\n" + completed.stderr).upper()
        return _status_from_output(output, completed.returncode, driver_pod)

    def cancel(self, job_id: str, driver_pod: str) -> None:
        del driver_pod
        master = self.profile.master_url(self.environ)
        if master is None:
            raise FatalError("Spark master is not configured")
        self._run([self.spark_submit, "--kill", job_id, "--master", master], "Spark cancel")

    def find(self, run_id: str, driver_pod: str) -> SparkSubmission | None:
        del run_id
        command = [
            self.kubectl,
            "get",
            "pod",
            driver_pod,
            "--namespace",
            self.profile.cluster.namespace,
            "--output",
            "json",
        ]
        kubeconfig = self.environ.get(self.profile.cluster.context_env)
        if kubeconfig:
            command.extend(["--kubeconfig", kubeconfig])
        completed = self._run(command, "Kubernetes reconcile", allow_failure=True)
        if completed.returncode != 0:
            return None
        try:
            payload = json.loads(completed.stdout)
            phase = str(payload["status"]["phase"])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RetryableError("Kubernetes returned an invalid driver pod status") from exc
        return SparkSubmission(
            job_id=driver_pod,
            driver_pod=driver_pod,
            status=_pod_phase_status(phase),
        )

    def _submit_command(self, application: SparkApplication) -> list[str]:
        command = [
            self.spark_submit,
            "--master",
            application.master,
            "--deploy-mode",
            "cluster",
            "--name",
            application.application_name,
            "--conf",
            f"spark.kubernetes.namespace={application.namespace}",
            "--conf",
            f"spark.kubernetes.container.image={application.driver_image}",
            "--conf",
            f"spark.kubernetes.driver.pod.name={application.driver_pod}",
            "--conf",
            "spark.kubernetes.authenticate.driver.serviceAccountName="
            f"{application.service_account}",
            "--conf",
            f"spark.executor.instances={application.executor_instances}",
            "--conf",
            f"spark.executor.cores={application.executor_cores}",
            "--conf",
            f"spark.executor.memory={application.executor_memory}",
        ]
        for key, value in sorted(application.labels.items()):
            command.extend(["--conf", f"spark.kubernetes.driver.label.{key}={value}"])
        for key, value in sorted(application.spark_conf.items()):
            command.extend(["--conf", f"{key}={value}"])
        command.extend(
            [
                application.worker_entrypoint,
                "--manifest",
                application.manifest_uri,
                "--manifest-hash",
                application.manifest_hash,
            ]
        )
        return command

    def _run(
        self,
        command: Sequence[str],
        operation: str,
        *,
        allow_failure: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                list(command),
                check=False,
                capture_output=True,
                text=True,
                env=self.environ,
            )
        except OSError as exc:
            raise RetryableError(f"{operation} command could not start") from exc
        if completed.returncode != 0 and not allow_failure:
            # Do not echo the command or process output: both may contain cluster
            # paths or provider diagnostics with sensitive context.
            raise RetryableError(f"{operation} failed with exit code {completed.returncode}")
        return completed


def driver_resource_name(run_id: str) -> str:
    return "arsenal-" + canonical_hash({"run_id": run_id})[:20]


def run_label_value(run_id: str) -> str:
    if _SAFE_LABEL.fullmatch(run_id):
        return run_id
    return canonical_hash({"run_id": run_id})[:32]


def handle_to_mapping(handle: JobHandle) -> dict[str, object]:
    return {
        "executor": handle.executor,
        "job_id": handle.job_id,
        "metadata": dict(handle.metadata or {}),
    }


def handle_from_mapping(value: Mapping[str, object]) -> JobHandle:
    executor = value.get("executor")
    job_id = value.get("job_id")
    metadata = value.get("metadata")
    if not isinstance(executor, str) or not executor:
        raise ValueError("external job executor is missing")
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("external job id is missing")
    typed_metadata: dict[str, object] | None = None
    if metadata is not None:
        if not isinstance(metadata, Mapping):
            raise ValueError("external job metadata must be a mapping")
        raw_metadata = cast(Mapping[object, object], metadata)
        if not all(isinstance(key, str) for key in raw_metadata):
            raise ValueError("external job metadata keys must be strings")
        typed_metadata = {cast(str, key): item for key, item in raw_metadata.items()}
    return JobHandle(executor=executor, job_id=job_id, metadata=typed_metadata)


def _driver_pod(handle: JobHandle) -> str:
    metadata = handle.metadata or {}
    value = metadata.get("driver_pod")
    if not isinstance(value, str) or not value:
        raise ValueError("Spark job handle is missing driver_pod metadata")
    return value


def _status_from_output(output: str, returncode: int, driver_pod: str) -> JobStatus:
    del driver_pod
    if "RUNNING" in output or "SUBMITTED" in output:
        return JobStatus.RUNNING if "RUNNING" in output else JobStatus.SUBMITTED
    if "SUCCEEDED" in output or "COMPLETED" in output:
        return JobStatus.SUCCEEDED
    if "CANCELLED" in output or "KILLED" in output:
        return JobStatus.CANCELLED
    if "FAILED" in output or "ERROR" in output:
        return JobStatus.FAILED
    return JobStatus.LOST if returncode != 0 else JobStatus.LOST


def _pod_phase_status(phase: str) -> JobStatus:
    return {
        "Pending": JobStatus.SUBMITTED,
        "Running": JobStatus.RUNNING,
        "Succeeded": JobStatus.SUCCEEDED,
        "Failed": JobStatus.FAILED,
    }.get(phase, JobStatus.LOST)


__all__ = [
    "SparkApplication",
    "SparkKubernetesClient",
    "SparkKubernetesExecutor",
    "SparkSubmission",
    "SubprocessSparkKubernetesClient",
    "driver_resource_name",
    "handle_from_mapping",
    "handle_to_mapping",
    "run_label_value",
]
