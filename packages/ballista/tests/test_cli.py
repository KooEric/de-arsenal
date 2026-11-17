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


def test_base_run_command_refuses_to_fake_remote_production_wiring(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    transform = tmp_path / "transform.yaml"
    write_profile(profile)
    write_transform(transform, "iceberg://analytics.daily")

    result = runner.invoke(
        app,
        [
            "run",
            str(transform),
            "--profile",
            str(profile),
            "--input-version",
            "snapshot-1",
            "--state-dir",
            str(tmp_path / "state"),
        ],
    )

    assert result.exit_code == 1
    assert "remote execution is not wired in the base" in result.output
    assert "never-echo-this-secret" not in result.output


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
