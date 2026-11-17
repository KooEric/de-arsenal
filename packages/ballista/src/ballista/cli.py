"""Ballista lifecycle CLI scaffolding with fail-closed production wiring."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, cast

import typer

from arsenal_core.errors import ArsenalError, FatalError
from arsenal_core.execution import (
    ArtifactRef,
    ExecutionRequest,
    GuaranteeRequest,
    LeaseToken,
    RunContext,
    spec_revision,
)
from arsenal_core.state import RunRecord, StateStore
from ballista.artifacts import ArtifactRepository
from ballista.config import BallistaProfile, load_profile
from ballista.executors.spark_kubernetes import (
    SparkKubernetesClient,
    SparkKubernetesExecutor,
    SubprocessSparkKubernetesClient,
)
from ballista.plan import plan_hash, plan_to_mapping
from ballista.preflight import (
    EnvironmentCapabilities,
    preflight_request,
    report_to_mapping,
)
from ballista.probe import KubernetesEnvironmentProbe
from ballista.runner import BallistaRunner
from ballista.storage import ObjectStoreBlobStore, fingerprint_uri
from gladius.loader import load_transform
from gladius.plan import preflight_portable

app = typer.Typer(
    no_args_is_help=True,
    help="Ballista — capability-gated remote execution of portable Gladius plans.",
)


def _state_path(state_dir: Path) -> Path:
    return state_dir / "ballista.sqlite"


@dataclass(frozen=True)
class Integration:
    """Production collaborators for one profile; replaced wholesale in tests."""

    artifacts: ArtifactRepository
    probe: KubernetesEnvironmentProbe
    client: SparkKubernetesClient


def build_integration(profile: BallistaProfile) -> Integration:
    """Object-store staging + live cluster probe + spark-submit/kubectl client.

    Requires ``de-ballista[object-store]``; ``spark-submit`` and ``kubectl`` are
    operator-provided binaries whose presence the probe verifies.
    """

    store = ObjectStoreBlobStore.from_staging_uri(profile.storage.staging_uri)
    return Integration(
        artifacts=ArtifactRepository(profile.storage.staging_uri, store),
        probe=KubernetesEnvironmentProbe(blob_store=store),
        client=SubprocessSparkKubernetesClient(profile),
    )


def _build_runner(profile: BallistaProfile, state_dir: Path) -> BallistaRunner:
    integration = build_integration(profile)
    executor = SparkKubernetesExecutor(
        profile=profile,
        artifacts=integration.artifacts,
        client=integration.client,
        environment_probe=integration.probe,
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    return BallistaRunner(
        state=StateStore.open(_state_path(state_dir)),
        executor=executor,
        artifacts=integration.artifacts,
    )


def _request_parts(
    transform_path: Path,
    profile_path: Path,
    input_version: str | None,
    run_id: str,
) -> tuple[BallistaProfile, ExecutionRequest]:
    profile = load_profile(profile_path)
    spec = load_transform(transform_path)
    logical_plan = preflight_portable(spec)
    serialized_plan = plan_to_mapping(logical_plan)
    now = datetime.now(UTC)
    request = ExecutionRequest(
        context=RunContext(
            run_id=run_id,
            pipeline=spec.name,
            revision=spec_revision(spec.model_dump(mode="json")),
            lease=LeaseToken(
                resource=f"run:{run_id}",
                owner_id="ballista-plan-only",
                fence=1,
                expires_at=now + timedelta(minutes=10),
            ),
            started_at=now,
        ),
        plan=serialized_plan,
        inputs=(
            ArtifactRef(
                spec.input,
                _artifact_format(spec.input),
                version=_resolve_input_version(spec.input, input_version),
            ),
        ),
        output=ArtifactRef(spec.output, _artifact_format(spec.output)),
        guarantees=GuaranteeRequest(result_exactly_once=True, atomic_publish=True),
    )
    return profile, request


@app.command("plan")
def plan_cmd(
    transform: Path,
    profile_path: Annotated[Path, typer.Option("--profile")],
    input_version: Annotated[str | None, typer.Option("--input-version")] = None,
    probe: Annotated[
        bool, typer.Option("--probe", help="Check the live cluster and staging store.")
    ] = False,
) -> None:
    """Render the portable request and report every unmet submission precondition."""

    try:
        profile, request = _request_parts(
            transform, profile_path, input_version, run_id=str(uuid.uuid4())
        )
        environment = EnvironmentCapabilities.unverified()
        findings: tuple[str, ...] = ("live checks skipped; pass --probe to verify",)
        if probe:
            result = build_integration(profile).probe.probe(profile)
            environment, findings = result.capabilities, result.findings
        report = preflight_request(
            request,
            profile=profile,
            executor_name="spark_kubernetes",
            executor_capabilities=_spark_capabilities(),
            environment=environment,
        )
        payload: dict[str, object] = {
            "pipeline": request.context.pipeline,
            "revision": request.context.revision,
            "plan_version": request.plan.get("version"),
            "plan_hash": plan_hash(request.plan),
            "plan_nodes": _plan_node_names(request.plan),
            "input": {
                "format": request.inputs[0].format,
                "version": request.inputs[0].version,
            },
            "output": {"format": request.output.format},
            "spark": {
                "executor_instances": profile.spark.executor_instances,
                "executor_cores": profile.spark.executor_cores,
                "executor_memory": profile.spark.executor_memory,
            },
            "preflight": {**report_to_mapping(report), "environment_findings": list(findings)},
        }
    except (ArsenalError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(payload, sort_keys=True))
    if not report.ready:
        raise typer.Exit(1)


@app.command("run")
def run_cmd(
    transform: Path,
    profile_path: Annotated[Path, typer.Option("--profile")],
    input_version: Annotated[
        str,
        typer.Option(
            "--input-version",
            help="Iceberg snapshot id, Parquet listing fingerprint, or 'auto' for Parquet.",
        ),
    ],
    state_dir: Annotated[Path, typer.Option("--state-dir")] = Path(".arsenal"),
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
) -> None:
    """Submit through an installed production integration (none is implicit)."""

    logical_run_id = run_id or str(uuid.uuid4())
    try:
        profile, request = _request_parts(transform, profile_path, input_version, logical_run_id)
        runner = _build_runner(profile, state_dir)
        record = runner.submit(
            pipeline=request.context.pipeline,
            revision=request.context.revision,
            plan=request.plan,
            inputs=request.inputs,
            output=request.output,
            guarantees=request.guarantees,
            run_id=logical_run_id,
        )
    except (ArsenalError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(_record_mapping(record), sort_keys=True))


@app.command("status")
def status_cmd(
    run_id: str,
    state_dir: Annotated[Path, typer.Option("--state-dir")] = Path(".arsenal"),
) -> None:
    """Show durable state without contacting or mutating the cluster."""

    state = StateStore.open(_state_path(state_dir))
    try:
        record = state.run(run_id)
    finally:
        state.close()
    if record is None:
        typer.echo(f"error: unknown Ballista run: {run_id}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(_record_mapping(record), sort_keys=True))


@app.command("reconcile")
def reconcile_cmd(
    run_id: str,
    profile_path: Annotated[Path, typer.Option("--profile")],
    state_dir: Annotated[Path, typer.Option("--state-dir")] = Path(".arsenal"),
) -> None:
    """Recover a driver/proof or safely create the next immutable attempt."""

    try:
        runner = _build_runner(load_profile(profile_path), state_dir)
        record = runner.reconcile(run_id)
    except (ArsenalError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(_record_mapping(record), sort_keys=True))


@app.command("cancel")
def cancel_cmd(
    run_id: str,
    profile_path: Annotated[Path, typer.Option("--profile")],
    state_dir: Annotated[Path, typer.Option("--state-dir")] = Path(".arsenal"),
) -> None:
    """Cancel a known/recovered Spark application and record the fenced result."""

    try:
        runner = _build_runner(load_profile(profile_path), state_dir)
        record = runner.cancel(run_id)
    except (ArsenalError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(_record_mapping(record), sort_keys=True))


def _resolve_input_version(uri: str, value: str | None) -> str | None:
    """``auto`` pins a Parquet prefix to its current listing fingerprint."""

    if value != "auto":
        return value
    if _artifact_format(uri) != "parquet":
        raise FatalError("--input-version auto is only supported for Parquet inputs")
    return fingerprint_uri(uri)


def _artifact_format(uri: str) -> Any:
    scheme = uri.partition("://")[0].lower() if "://" in uri else ""
    if scheme == "iceberg":
        return "iceberg"
    if scheme in {"postgres", "postgresql"}:
        return "postgres"
    if scheme == "duckdb":
        return "duckdb"
    return "parquet"


def _spark_capabilities() -> Any:
    return SparkKubernetesExecutor.capabilities


def _record_mapping(record: RunRecord) -> dict[str, object]:
    return {
        "run_id": record.run_id,
        "pipeline": record.pipeline,
        "revision": record.revision,
        "mode": record.mode,
        "executor": record.executor,
        "status": record.status,
        "attempt": record.attempt,
        "external_job": dict(record.external_job) if record.external_job is not None else None,
        "error": record.error,
    }


def _plan_node_names(plan: Mapping[str, Any]) -> list[str]:
    raw_nodes = plan.get("nodes")
    if not isinstance(raw_nodes, list):
        return []
    names: list[str] = []
    for item in cast(list[object], raw_nodes):
        if not isinstance(item, Mapping):
            continue
        mapping = cast(Mapping[object, object], item)
        node_type = mapping.get("type")
        if isinstance(node_type, str):
            names.append(node_type)
    return names


if __name__ == "__main__":
    app()
