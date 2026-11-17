from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ballista.config import BallistaProfile, load_profile
from ballista.executors.spark_kubernetes import SparkKubernetesExecutor
from ballista.plan import plan_to_mapping
from ballista.preflight import EnvironmentCapabilities, preflight_request

from arsenal_core.errors import FatalError
from arsenal_core.execution import (
    ArtifactRef,
    ExecutionRequest,
    GuaranteeRequest,
    LeaseToken,
    RunContext,
)
from gladius.plan import preflight_portable
from gladius.spec import TransformSpec


def profile() -> BallistaProfile:
    return BallistaProfile.model_validate(
        {
            "backend": "spark_kubernetes",
            "cluster": {
                "context_env": "KUBECONFIG",
                "namespace": "data-jobs",
                "driver_image": "registry.example/arsenal-spark:0.3.0",
                "service_account": "arsenal",
                "master": "k8s://https://cluster.example",
            },
            "storage": {
                "staging_uri": "s3://lake/_arsenal/staging",
                "iceberg_catalog": {
                    "type": "rest",
                    "uri_env": "ICEBERG_REST_URI",
                    "token_env": "ICEBERG_REST_TOKEN",
                },
            },
            "spark": {"executor_instances": 2, "executor_cores": 1, "executor_memory": "2g"},
        }
    )


def request(output: ArtifactRef) -> ExecutionRequest:
    now = datetime(2026, 8, 22, tzinfo=UTC)
    spec = TransformSpec.model_validate(
        {
            "name": "daily",
            "input": "s3://lake/input",
            "steps": [{"filter": "amount > 0"}],
            "output": output.uri,
        }
    )
    return ExecutionRequest(
        context=RunContext(
            run_id="run-1",
            pipeline="daily",
            revision="revision-1",
            lease=LeaseToken("run:run-1", "owner", 1, now + timedelta(minutes=5)),
            started_at=now,
        ),
        plan=plan_to_mapping(preflight_portable(spec)),
        inputs=(ArtifactRef("s3://lake/input", "parquet", version="snapshot-7"),),
        output=output,
        guarantees=GuaranteeRequest(result_exactly_once=True, atomic_publish=True),
    )


def test_profile_loads_env_references_without_interpolating_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "do-not-print-this-token"
    monkeypatch.setenv("KUBECONFIG", "/tmp/kubeconfig")
    monkeypatch.setenv("ICEBERG_REST_URI", "https://catalog.example")
    monkeypatch.setenv("ICEBERG_REST_TOKEN", secret)
    path = tmp_path / "profile.yaml"
    path.write_text(
        """backend: spark_kubernetes
cluster:
  context_env: KUBECONFIG
  namespace: data-jobs
  driver_image: registry.example/arsenal-spark:0.3.0
  service_account: arsenal
  master: k8s://https://cluster.example
storage:
  staging_uri: s3://lake/_arsenal/staging
  iceberg_catalog:
    type: rest
    uri_env: ICEBERG_REST_URI
    token_env: ICEBERG_REST_TOKEN
spark:
  conf:
    spark.sql.shuffle.partitions: 8
""",
        encoding="utf-8",
    )

    loaded = load_profile(path)

    assert loaded.storage.iceberg_catalog is not None
    assert loaded.storage.iceberg_catalog.token_env == "ICEBERG_REST_TOKEN"
    assert secret not in repr(loaded)


def test_profile_rejects_secret_bearing_or_unallowlisted_spark_conf(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.yaml"
    path.write_text(
        """backend: spark_kubernetes
cluster:
  context_env: KUBECONFIG
  namespace: jobs
  driver_image: image
  service_account: arsenal
storage: {staging_uri: s3://lake/staging}
spark:
  conf: {spark.jars.packages: arbitrary:package:1}
""",
        encoding="utf-8",
    )

    with pytest.raises(FatalError, match="not allowlisted|not permitted"):
        load_profile(path)


def test_preflight_accepts_only_versioned_input_and_atomic_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KUBECONFIG", "/tmp/kubeconfig")
    monkeypatch.setenv("ICEBERG_REST_URI", "https://catalog.example")
    monkeypatch.setenv("ICEBERG_REST_TOKEN", "secret")
    good = preflight_request(
        request(ArtifactRef("iceberg://analytics.daily", "iceberg")),
        profile=profile(),
        executor_name="spark_kubernetes",
        executor_capabilities=SparkKubernetesExecutor.capabilities,
        environment=EnvironmentCapabilities.verified(),
    )
    assert good.ready
    assert good.output_capabilities.atomic_publish
    assert good.output_capabilities.concurrent_writers

    unsafe_request = request(ArtifactRef("s3://lake/output", "parquet"))
    unsafe = preflight_request(
        unsafe_request,
        profile=profile(),
        executor_name="spark_kubernetes",
        executor_capabilities=SparkKubernetesExecutor.capabilities,
        environment=EnvironmentCapabilities.verified(),
    )
    assert not unsafe.ready
    assert "output.atomic_commit" in {issue.code for issue in unsafe.issues}
    assert "raw Parquet" in " ".join(issue.message for issue in unsafe.issues)


def test_preflight_rejects_local_and_unversioned_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUBECONFIG", "/tmp/kubeconfig")
    monkeypatch.setenv("ICEBERG_REST_URI", "https://catalog.example")
    monkeypatch.setenv("ICEBERG_REST_TOKEN", "secret")
    value = request(ArtifactRef("iceberg://analytics.daily", "iceberg"))
    value = ExecutionRequest(
        context=value.context,
        plan=value.plan,
        inputs=(ArtifactRef("file:///tmp/input", "parquet"),),
        output=value.output,
        guarantees=value.guarantees,
    )

    report = preflight_request(
        value,
        profile=profile(),
        executor_name="spark_kubernetes",
        executor_capabilities=SparkKubernetesExecutor.capabilities,
        environment=EnvironmentCapabilities.verified(),
    )

    assert {issue.code for issue in report.issues} >= {"input.shared_uri", "input.version"}
