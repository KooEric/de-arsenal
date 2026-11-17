from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ballista.artifacts import ArtifactRepository, InMemoryBlobStore, RunManifest
from ballista.config import BallistaProfile
from ballista.executors.spark_kubernetes import (
    SparkApplication,
    SparkKubernetesExecutor,
    SparkSubmission,
)
from ballista.plan import plan_hash, plan_to_mapping
from ballista.preflight import EnvironmentCapabilities
from ballista.runner import BallistaRunner

from arsenal_core.execution import ArtifactRef, JobStatus, OutputProof
from arsenal_core.state import StateStore
from gladius.plan import preflight_portable
from gladius.spec import TransformSpec


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 22, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


class Probe:
    def inspect(self, profile: BallistaProfile) -> EnvironmentCapabilities:
        del profile
        return EnvironmentCapabilities.verified()


class MockClient:
    def __init__(self) -> None:
        self.jobs: dict[str, SparkSubmission] = {}
        self.submit_calls: list[SparkApplication] = []
        self.cancelled: list[str] = []

    def submit(self, application: SparkApplication) -> SparkSubmission:
        self.submit_calls.append(application)
        submission = SparkSubmission(
            job_id=application.driver_pod,
            driver_pod=application.driver_pod,
            status=JobStatus.SUBMITTED,
        )
        self.jobs[application.driver_pod] = submission
        return submission

    def status(self, job_id: str, driver_pod: str) -> JobStatus:
        del job_id
        found = self.jobs.get(driver_pod)
        return JobStatus.LOST if found is None else found.status

    def cancel(self, job_id: str, driver_pod: str) -> None:
        del job_id
        self.cancelled.append(driver_pod)
        self.jobs[driver_pod] = replace(self.jobs[driver_pod], status=JobStatus.CANCELLED)

    def find(self, run_id: str, driver_pod: str) -> SparkSubmission | None:
        del run_id
        return self.jobs.get(driver_pod)

    def set_status(self, driver_pod: str, status: JobStatus) -> None:
        self.jobs[driver_pod] = replace(self.jobs[driver_pod], status=status)


@pytest.fixture(autouse=True)
def profile_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUBECONFIG", "/tmp/kubeconfig")
    monkeypatch.setenv("ICEBERG_REST_URI", "https://catalog.example")
    monkeypatch.setenv("ICEBERG_REST_TOKEN", "secret")


def profile() -> BallistaProfile:
    return BallistaProfile.model_validate(
        {
            "backend": "spark_kubernetes",
            "cluster": {
                "context_env": "KUBECONFIG",
                "namespace": "jobs",
                "driver_image": "registry.example/spark:0.3.0",
                "service_account": "arsenal",
                "master": "k8s://https://cluster.example",
            },
            "storage": {
                "staging_uri": "s3://lake/staging",
                "iceberg_catalog": {
                    "type": "rest",
                    "uri_env": "ICEBERG_REST_URI",
                    "token_env": "ICEBERG_REST_TOKEN",
                },
            },
        }
    )


def portable_plan() -> dict[str, object]:
    spec = TransformSpec.model_validate(
        {
            "name": "daily",
            "input": "s3://lake/input",
            "steps": [{"filter": "amount > 0"}, {"select": ["id", "amount"]}],
            "output": "iceberg://analytics.daily",
        }
    )
    return plan_to_mapping(preflight_portable(spec))


def harness(
    tmp_path: Path,
    *,
    client: MockClient | None = None,
    artifacts: ArtifactRepository | None = None,
    state: StateStore | None = None,
    clock: Clock | None = None,
    fault_hook: Callable[[str], None] | None = None,
    owner_id: str = "runner-one",
) -> tuple[BallistaRunner, MockClient, ArtifactRepository, StateStore, Clock]:
    actual_clock = clock or Clock()
    actual_state = state or StateStore(tmp_path / "state.db", clock=actual_clock)
    actual_artifacts = artifacts or ArtifactRepository("s3://lake/staging", InMemoryBlobStore())
    actual_client = client or MockClient()
    executor = SparkKubernetesExecutor(
        profile=profile(),
        artifacts=actual_artifacts,
        client=actual_client,
        environment_probe=Probe(),
    )
    runner = BallistaRunner(
        state=actual_state,
        executor=executor,
        artifacts=actual_artifacts,
        owner_id=owner_id,
        clock=actual_clock,
        fault_hook=fault_hook,
    )
    return runner, actual_client, actual_artifacts, actual_state, actual_clock


def submit(runner: BallistaRunner, run_id: str = "run-1"):
    return runner.submit(
        pipeline="daily",
        revision="rev-1",
        plan=portable_plan(),
        inputs=(ArtifactRef("s3://lake/input", "parquet", version="snapshot-1"),),
        output=ArtifactRef("iceberg://analytics.daily", "iceberg"),
        run_id=run_id,
    )


def output_proof(manifest: RunManifest, clock: Clock) -> OutputProof:
    return OutputProof(
        run_id=manifest.request.context.run_id,
        revision=manifest.request.context.revision,
        output=replace(manifest.request.output, version="snapshot-out-1"),
        input_versions=("snapshot-1",),
        row_count=2,
        schema_hash="schema",
        plan_hash=plan_hash(manifest.request.plan),
        started_at=clock(),
        finished_at=clock() + timedelta(seconds=2),
        output_version="snapshot-out-1",
    )


def test_submit_running_proof_succeeded_lifecycle(tmp_path: Path) -> None:
    runner, client, artifacts, _state, clock = harness(tmp_path)
    record = submit(runner)
    assert record.status == "submitted"
    assert record.external_job is not None
    driver = str(record.external_job["metadata"]["driver_pod"])  # type: ignore[index]

    client.set_status(driver, JobStatus.RUNNING)
    assert runner.reconcile("run-1").status == "running"

    manifest = artifacts.read_manifest("run-1", 0)
    artifacts.write_output_proof(manifest, output_proof(manifest, clock))
    finished = runner.reconcile("run-1")
    assert finished.status == "succeeded"
    assert len(client.submit_calls) == 1


def test_crash_after_remote_submit_recovers_handle_without_duplicate_submit(tmp_path: Path) -> None:
    def crash(stage: str) -> None:
        if stage == "after_submit":
            raise SystemExit("simulated coordinator crash")

    runner, client, artifacts, state, clock = harness(tmp_path, fault_hook=crash)
    with pytest.raises(SystemExit):
        submit(runner)
    assert state.run("run-1").status == "planned"  # type: ignore[union-attr]
    assert len(client.submit_calls) == 1

    recovered, _, _, _, _ = harness(
        tmp_path,
        client=client,
        artifacts=artifacts,
        state=state,
        clock=clock,
        owner_id="runner-two",
    )
    record = recovered.reconcile("run-1")

    assert record.status == "submitted"
    assert record.external_job is not None
    assert len(client.submit_calls) == 1


def test_reconcile_reuses_immutable_attempt_after_crash_before_submit(tmp_path: Path) -> None:
    def crash(stage: str) -> None:
        if stage == "after_run_planned":
            raise SystemExit("simulated coordinator crash")

    runner, client, artifacts, state, clock = harness(tmp_path, fault_hook=crash)
    with pytest.raises(SystemExit):
        submit(runner)
    assert not client.submit_calls

    recovered, _, _, _, _ = harness(
        tmp_path,
        client=client,
        artifacts=artifacts,
        state=state,
        clock=clock,
        owner_id="runner-two",
    )
    record = recovered.reconcile("run-1")

    assert record.status == "submitted"
    assert record.attempt == 0
    assert len(client.submit_calls) == 1
    assert artifacts.read_manifest("run-1", 0).attempt == 0


def test_reconcile_plans_new_attempt_after_known_job_is_absent(tmp_path: Path) -> None:
    runner, client, artifacts, state, _clock = harness(tmp_path)
    record = submit(runner)
    driver = str(record.external_job["metadata"]["driver_pod"])  # type: ignore[index,union-attr]
    del client.jobs[driver]

    retried = runner.reconcile("run-1")

    assert retried.status == "submitted"
    assert retried.attempt == 1
    assert len(client.submit_calls) == 2
    assert artifacts.read_manifest("run-1", 0).attempt == 0
    assert artifacts.read_manifest("run-1", 1).attempt == 1
    assert state.run("run-1").attempt == 1  # type: ignore[union-attr]


def test_driver_success_without_proof_is_lost_not_succeeded(tmp_path: Path) -> None:
    runner, client, _artifacts, _state, _clock = harness(tmp_path)
    record = submit(runner)
    driver = str(record.external_job["metadata"]["driver_pod"])  # type: ignore[index,union-attr]
    client.set_status(driver, JobStatus.SUCCEEDED)

    reconciled = runner.reconcile("run-1")

    assert reconciled.status == "lost"
    assert "proof is missing" in (reconciled.error or "")

    del client.jobs[driver]
    still_lost = runner.reconcile("run-1")
    assert still_lost.status == "lost"
    assert len(client.submit_calls) == 1


def test_crash_after_retry_is_planned_reuses_same_attempt_manifest(tmp_path: Path) -> None:
    runner, client, artifacts, state, clock = harness(tmp_path)
    record = submit(runner)
    driver = str(record.external_job["metadata"]["driver_pod"])  # type: ignore[index,union-attr]
    del client.jobs[driver]

    def crash(stage: str) -> None:
        if stage == "after_retry_planned":
            raise SystemExit("simulated retry coordinator crash")

    crashing, _, _, _, _ = harness(
        tmp_path,
        client=client,
        artifacts=artifacts,
        state=state,
        clock=clock,
        fault_hook=crash,
        owner_id="runner-two",
    )
    with pytest.raises(SystemExit):
        crashing.reconcile("run-1")
    planned = state.run("run-1")
    assert planned is not None
    assert planned.status == "planned"
    assert planned.attempt == 1

    recovered, _, _, _, _ = harness(
        tmp_path,
        client=client,
        artifacts=artifacts,
        state=state,
        clock=clock,
        owner_id="runner-three",
    )
    retried = recovered.reconcile("run-1")
    assert retried.status == "submitted"
    assert retried.attempt == 1
    assert len(client.submit_calls) == 2


def test_cancel_uses_stable_handle_and_records_terminal_state(tmp_path: Path) -> None:
    runner, client, _artifacts, _state, _clock = harness(tmp_path)
    record = submit(runner)
    driver = str(record.external_job["metadata"]["driver_pod"])  # type: ignore[index,union-attr]

    cancelled = runner.cancel("run-1")

    assert cancelled.status == "cancelled"
    assert client.cancelled == [driver]
