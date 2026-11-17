import json
from pathlib import Path

import pytest
from ballista.cli import app
from typer.testing import CliRunner

from arsenal_core.state import StateStore

runner = CliRunner()


def write_profile(path: Path) -> None:
    path.write_text(
        """backend: spark_kubernetes
cluster:
  context_env: KUBECONFIG
  namespace: jobs
  driver_image: registry.example/spark:0.3.0
  service_account: arsenal
  master: k8s://https://cluster.example
storage:
  staging_uri: s3://lake/staging
  iceberg_catalog:
    type: rest
    uri_env: ICEBERG_REST_URI
    token_env: ICEBERG_REST_TOKEN
spark: {executor_instances: 2, executor_cores: 1, executor_memory: 2g}
""",
        encoding="utf-8",
    )


def write_transform(path: Path, output: str) -> None:
    path.write_text(
        f"""name: daily
input: s3://lake/input
steps:
  - filter: amount > 0
output: {output}
""",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def runtime_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUBECONFIG", "/tmp/kubeconfig")
    monkeypatch.setenv("ICEBERG_REST_URI", "https://catalog.example")
    monkeypatch.setenv("ICEBERG_REST_TOKEN", "never-echo-this-secret")


def test_plan_prints_safe_capabilities_and_unverified_remote_checks(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    transform = tmp_path / "transform.yaml"
    write_profile(profile)
    write_transform(transform, "iceberg://analytics.daily")

    result = runner.invoke(
        app,
        [
            "plan",
            str(transform),
            "--profile",
            str(profile),
            "--input-version",
            "snapshot-1",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["plan_nodes"] == ["read_parquet", "filter", "write_parquet"]
    assert payload["output"] == {"format": "iceberg"}
    assert payload["preflight"]["ready"] is False
    assert "environment.cluster" in {issue["code"] for issue in payload["preflight"]["issues"]}
    assert "never-echo-this-secret" not in result.output


def test_plan_explicitly_rejects_raw_parquet_production_output(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    transform = tmp_path / "transform.yaml"
    write_profile(profile)
    write_transform(transform, "s3://lake/output")

    result = runner.invoke(
        app,
        [
            "plan",
            str(transform),
            "--profile",
            str(profile),
            "--input-version",
            "snapshot-1",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    issues = payload["preflight"]["issues"]
    assert "output.atomic_commit" in {issue["code"] for issue in issues}
    assert any("raw Parquet" in issue["message"] for issue in issues)


def _run_args(
    transform: Path, profile: Path, state: Path, version: str = "snapshot-1"
) -> list[str]:
    return [
        "run",
        str(transform),
        "--profile",
        str(profile),
        "--input-version",
        version,
        "--state-dir",
        str(state),
        "--run-id",
        "run-cli-1",
    ]


def test_run_without_object_store_extra_explains_how_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ballista.storage import BlobStoreError, ObjectStoreBlobStore

    def missing_extra(*_args: object, **_kwargs: object) -> ObjectStoreBlobStore:
        raise BlobStoreError(
            "remote staging requires the object-store extra: "
            "pip install 'de-ballista[object-store]'"
        )

    monkeypatch.setattr(ObjectStoreBlobStore, "from_staging_uri", missing_extra)
    profile, transform = tmp_path / "profile.yaml", tmp_path / "transform.yaml"
    write_profile(profile)
    write_transform(transform, "iceberg://analytics.daily")

    result = runner.invoke(app, _run_args(transform, profile, tmp_path / "state"))

    assert result.exit_code == 1
    assert "de-ballista[object-store]" in result.output
    assert "never-echo-this-secret" not in result.output


class _FakeSparkClient:
    def __init__(self) -> None:
        self.submitted: list[object] = []

    def submit(self, application: object) -> object:
        from ballista.executors.spark_kubernetes import SparkSubmission

        self.submitted.append(application)
        return SparkSubmission(job_id="driver-1", driver_pod=application.driver_pod)  # type: ignore[attr-defined]

    def status(self, job_id: str, driver_pod: str) -> object:
        from arsenal_core.execution import JobStatus

        return JobStatus.RUNNING

    def cancel(self, job_id: str, driver_pod: str) -> None:
        return None

    def find(self, run_id: str, driver_pod: str) -> object:
        return None


def _healthy_runner(command: object, environ: object, timeout: float) -> object:
    from ballista.probe import CommandResult

    argv = list(command)  # type: ignore[call-overload]
    if "--raw" in argv:
        return CommandResult(0, "ok\n")
    if "can-i" in argv:
        return CommandResult(0, "yes\n")
    return CommandResult(0, "")


@pytest.fixture
def fake_integration(monkeypatch: pytest.MonkeyPatch) -> _FakeSparkClient:
    import ballista.cli as cli
    from ballista.artifacts import ArtifactRepository, InMemoryBlobStore
    from ballista.probe import KubernetesEnvironmentProbe

    client = _FakeSparkClient()
    store = InMemoryBlobStore()

    def build(profile: object) -> object:
        return cli.Integration(
            artifacts=ArtifactRepository("s3://lake/staging", store),
            probe=KubernetesEnvironmentProbe(
                blob_store=store,
                runner=_healthy_runner,  # type: ignore[arg-type]
                catalog_check=lambda _p, _e: True,
            ),
            client=client,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(cli, "build_integration", build)
    return client


def write_digest_profile(path: Path) -> None:
    write_profile(path)
    text = path.read_text(encoding="utf-8").replace(
        "registry.example/spark:0.3.0", "registry.example/spark@sha256:" + "b" * 64
    )
    path.write_text(text, encoding="utf-8")


def test_run_submits_through_wired_integration_and_records_the_run(
    tmp_path: Path, fake_integration: _FakeSparkClient
) -> None:
    profile, transform = tmp_path / "profile.yaml", tmp_path / "transform.yaml"
    write_digest_profile(profile)
    write_transform(transform, "iceberg://analytics.daily")

    result = runner.invoke(app, _run_args(transform, profile, tmp_path / "state"))

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["run_id"] == "run-cli-1"
    assert len(fake_integration.submitted) == 1
    assert "never-echo-this-secret" not in result.output


def test_run_is_blocked_when_live_probe_fails(
    tmp_path: Path, fake_integration: _FakeSparkClient
) -> None:
    profile, transform = tmp_path / "profile.yaml", tmp_path / "transform.yaml"
    write_profile(profile)  # mutable image tag, no opt-in
    write_transform(transform, "iceberg://analytics.daily")

    result = runner.invoke(app, _run_args(transform, profile, tmp_path / "state"))

    assert result.exit_code == 1
    assert "environment.image" in result.output
    assert fake_integration.submitted == []


def test_plan_probe_reports_live_findings(
    tmp_path: Path, fake_integration: _FakeSparkClient
) -> None:
    profile, transform = tmp_path / "profile.yaml", tmp_path / "transform.yaml"
    write_profile(profile)
    write_transform(transform, "iceberg://analytics.daily")

    result = runner.invoke(
        app,
        ["plan", str(transform), "--profile", str(profile), "--input-version", "1", "--probe"],
    )

    payload = json.loads(result.stdout)
    codes = {issue["code"] for issue in payload["preflight"]["issues"]}
    assert codes == {"environment.image"}
    assert any("digest-pinned" in f for f in payload["preflight"]["environment_findings"])


def test_input_version_auto_is_refused_for_non_parquet_inputs(tmp_path: Path) -> None:
    profile, transform = tmp_path / "profile.yaml", tmp_path / "transform.yaml"
    write_profile(profile)
    transform.write_text(
        "name: daily\ninput: iceberg://raw.orders\nsteps:\n  - filter: amount > 0\n"
        "output: iceberg://analytics.daily\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["plan", str(transform), "--profile", str(profile), "--input-version", "auto"]
    )

    assert result.exit_code == 1
    assert "only supported for Parquet" in result.output


def test_status_is_a_read_only_durable_ledger_view(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state = StateStore.open(state_dir / "ballista.sqlite")
    state.create_run(
        run_id="run-1",
        pipeline="daily",
        revision="rev-1",
        mode="transform",
        executor="spark_kubernetes",
        guarantees={"atomic_publish": True},
    )
    state.close()

    result = runner.invoke(app, ["status", "run-1", "--state-dir", str(state_dir)])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "planned"
