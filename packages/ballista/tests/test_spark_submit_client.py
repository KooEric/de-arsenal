from __future__ import annotations

import subprocess
from typing import Any

import pytest
from ballista.config import BallistaProfile
from ballista.executors.spark_kubernetes import (
    SparkApplication,
    SubprocessSparkKubernetesClient,
)

from arsenal_core.errors import RetryableError
from arsenal_core.execution import JobStatus

PROFILE = BallistaProfile.model_validate(
    {
        "backend": "spark_kubernetes",
        "cluster": {
            "context_env": "KUBECONFIG",
            "namespace": "jobs",
            "driver_image": "img@sha256:" + "c" * 64,
            "service_account": "arsenal",
            "master": "k8s://https://cluster.example",
        },
        "storage": {"staging_uri": "s3://lake/staging", "transactional_database": True},
    }
)

APPLICATION = SparkApplication(
    run_id="run-1",
    attempt=0,
    application_name="arsenal-abc",
    driver_pod="arsenal-abc",
    manifest_uri="s3://lake/staging/runs/run-1/attempt-0/manifest.json",
    manifest_hash="h" * 64,
    master="k8s://https://cluster.example",
    namespace="jobs",
    driver_image="img@sha256:" + "c" * 64,
    service_account="arsenal",
    worker_entrypoint="local:///opt/de-arsenal/ballista-worker.py",
    labels={"de-arsenal.io/run-id": "run-1"},
    spark_conf={"spark.sql.shuffle.partitions": 8},
    executor_instances=2,
    executor_cores=1,
    executor_memory="2g",
)


class Recorder:
    def __init__(self, *, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.result = (stdout, stderr, returncode)

    def __call__(self, command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        stdout, stderr, code = self.result
        return subprocess.CompletedProcess(command, code, stdout, stderr)


def client(monkeypatch: pytest.MonkeyPatch, recorder: Recorder) -> SubprocessSparkKubernetesClient:
    monkeypatch.setattr(subprocess, "run", recorder)
    return SubprocessSparkKubernetesClient(PROFILE, environ={"KUBECONFIG": "/k"})


def test_submission_id_is_read_from_spark_stderr_log(monkeypatch: pytest.MonkeyPatch) -> None:
    log = (
        "25/11/17 INFO KubernetesClientUtils: ...\n"
        "25/11/17 INFO LoggingPodStatusWatcherImpl: Deployed Spark application arsenal-abc "
        "with application ID spark-123 and submission ID jobs:arsenal-abc into Kubernetes\n"
    )
    recorder = Recorder(stderr=log)

    submission = client(monkeypatch, recorder).submit(APPLICATION)

    assert submission.job_id == "jobs:arsenal-abc"


def test_missing_log_line_falls_back_to_namespaced_driver_pod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submission = client(monkeypatch, Recorder()).submit(APPLICATION)

    assert submission.job_id == "jobs:arsenal-abc"


def test_submit_returns_without_waiting_for_job_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder()
    client(monkeypatch, recorder).submit(APPLICATION)

    command = recorder.calls[0]
    assert "spark.kubernetes.submission.waitAppCompletion=false" in command
    assert command[command.index("--deploy-mode") + 1] == "cluster"
    assert command[-4:] == [
        "--manifest",
        APPLICATION.manifest_uri,
        "--manifest-hash",
        APPLICATION.manifest_hash,
    ]


def test_failed_submit_does_not_echo_process_output(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(stderr="token=SECRET-VALUE", returncode=1)

    with pytest.raises(RetryableError) as caught:
        client(monkeypatch, recorder).submit(APPLICATION)
    assert "SECRET-VALUE" not in str(caught.value)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("Application status (driver): phase: Running", JobStatus.RUNNING),
        ("Application status (driver): phase: Succeeded", JobStatus.SUCCEEDED),
    ],
)
def test_status_uses_namespaced_submission_id(
    monkeypatch: pytest.MonkeyPatch, output: str, expected: JobStatus
) -> None:
    recorder = Recorder(stderr=output)

    status = client(monkeypatch, recorder).status("jobs:arsenal-abc", "arsenal-abc")

    assert status is expected
    assert recorder.calls[0][:3] == ["spark-submit", "--status", "jobs:arsenal-abc"]
