"""Fail-closed capability and remote-environment preflight."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from arsenal_core.errors import FatalError
from arsenal_core.execution import (
    ArtifactRef,
    ExecutionRequest,
    ExecutorCapabilities,
    SinkCapabilities,
)
from ballista.config import BallistaProfile
from ballista.plan import PlanFormatError, require_portable_mapping


class PreflightError(FatalError):
    """The request is unsafe or the remote environment is not proven ready."""


@dataclass(frozen=True)
class PreflightIssue:
    code: str
    message: str


@dataclass(frozen=True)
class EnvironmentCapabilities:
    """Results of real cluster/storage checks, supplied by an integration adapter."""

    spark_submit_available: bool
    cluster_reachable: bool
    rbac_create_get_list_delete: bool
    staging_read: bool
    staging_write: bool
    catalog_credentials: bool
    image_compatible: bool

    @classmethod
    def verified(cls) -> EnvironmentCapabilities:
        """Convenience for mocked tests; production probes must establish each fact."""

        return cls(
            spark_submit_available=True,
            cluster_reachable=True,
            rbac_create_get_list_delete=True,
            staging_read=True,
            staging_write=True,
            catalog_credentials=True,
            image_compatible=True,
        )

    @classmethod
    def unverified(cls) -> EnvironmentCapabilities:
        return cls(
            spark_submit_available=False,
            cluster_reachable=False,
            rbac_create_get_list_delete=False,
            staging_read=False,
            staging_write=False,
            catalog_credentials=False,
            image_compatible=False,
        )


class EnvironmentProbe(Protocol):
    def inspect(self, profile: BallistaProfile) -> EnvironmentCapabilities: ...


class UnverifiedEnvironmentProbe:
    """Base-package default: no remote claims without a concrete integration."""

    def inspect(self, profile: BallistaProfile) -> EnvironmentCapabilities:
        del profile
        return EnvironmentCapabilities.unverified()


@dataclass(frozen=True)
class PreflightReport:
    executor: str
    executor_capabilities: ExecutorCapabilities
    output_capabilities: SinkCapabilities
    environment: EnvironmentCapabilities
    issues: tuple[PreflightIssue, ...]

    @property
    def ready(self) -> bool:
        return not self.issues

    def require(self) -> None:
        if self.issues:
            details = "\n".join(f"- [{issue.code}] {issue.message}" for issue in self.issues)
            raise PreflightError("Ballista preflight failed:\n" + details)


def output_capabilities(profile: BallistaProfile, output: ArtifactRef) -> SinkCapabilities:
    """Advertise only commits the configured storage can actually make atomic."""

    if output.format == "iceberg" and profile.storage.iceberg_catalog is not None:
        return SinkCapabilities(
            idempotent_batch_write=True,
            atomic_publish=True,
            delete_apply=True,
            concurrent_writers=True,
            input_versioning=True,
        )
    if output.format == "postgres" and profile.storage.transactional_database:
        return SinkCapabilities(
            idempotent_batch_write=True,
            atomic_publish=True,
            delete_apply=True,
            concurrent_writers=True,
            input_versioning=True,
        )
    return SinkCapabilities(
        # Deterministic Parquet keys can be idempotent, but raw object-store
        # Parquet still cannot atomically publish a distributed result.
        idempotent_batch_write=output.format == "parquet",
    )


def preflight_request(
    request: ExecutionRequest,
    *,
    profile: BallistaProfile,
    executor_name: str,
    executor_capabilities: ExecutorCapabilities,
    environment: EnvironmentCapabilities,
) -> PreflightReport:
    issues: list[PreflightIssue] = []
    try:
        require_portable_mapping(request.plan)
    except (PlanFormatError, ValueError) as exc:
        issues.append(PreflightIssue("plan.portability", str(exc)))

    if not request.inputs:
        issues.append(PreflightIssue("input.missing", "at least one input artifact is required"))
    for index, artifact in enumerate(request.inputs):
        if not _shared_uri(artifact.uri):
            issues.append(
                PreflightIssue(
                    "input.shared_uri",
                    f"input {index} must use a worker-accessible URI; "
                    "local paths and file:// are forbidden",
                )
            )
        if not artifact.version:
            issues.append(
                PreflightIssue(
                    "input.version",
                    f"input {index} has no immutable snapshot/manifest version",
                )
            )

    if not _shared_uri(request.output.uri):
        issues.append(
            PreflightIssue(
                "output.shared_uri",
                "output must use a worker-accessible URI; local paths and file:// are forbidden",
            )
        )
    if not _format_matches_uri(request.output):
        issues.append(
            PreflightIssue(
                "output.format_uri",
                f"output format {request.output.format!r} does not match its URI scheme",
            )
        )

    missing_executor = executor_capabilities.missing(request.guarantees)
    for capability in missing_executor:
        issues.append(
            PreflightIssue(
                "executor.capability",
                f"executor {executor_name!r} does not provide requested {capability}",
            )
        )

    sink = output_capabilities(profile, request.output)
    if not sink.supports(request.guarantees):
        issues.append(
            PreflightIssue(
                "output.guarantee",
                "output capabilities are weaker than the requested execution guarantees",
            )
        )
    # This is unconditional for Ballista v1. A caller cannot obtain a misleading
    # "production" submission by setting GuaranteeRequest fields to False.
    if not (sink.atomic_publish and sink.concurrent_writers and sink.idempotent_batch_write):
        issues.append(
            PreflightIssue(
                "output.atomic_commit",
                "production Ballista requires an atomic/transactional, "
                "concurrent-writer-safe output; raw Parquet is not eligible",
            )
        )

    missing_env = profile.missing_environment()
    if missing_env:
        issues.append(
            PreflightIssue(
                "profile.environment",
                "required environment variables are not set: " + ", ".join(missing_env),
            )
        )
    if profile.master_url() is None:
        issues.append(
            PreflightIssue(
                "profile.master",
                "cluster.master or cluster.master_env must resolve to a k8s:// Spark master",
            )
        )
    checks: tuple[tuple[str, bool, str], ...] = (
        (
            "environment.spark_submit",
            environment.spark_submit_available,
            "spark-submit is unavailable",
        ),
        (
            "environment.cluster",
            environment.cluster_reachable,
            "Kubernetes cluster is not reachable",
        ),
        (
            "environment.rbac",
            environment.rbac_create_get_list_delete,
            "Kubernetes create/get/list/delete permissions were not verified",
        ),
        (
            "environment.staging_read",
            environment.staging_read,
            "staging read access was not verified",
        ),
        (
            "environment.staging_write",
            environment.staging_write,
            "staging conditional-write access was not verified",
        ),
        (
            "environment.catalog",
            environment.catalog_credentials,
            "transactional catalog credentials were not verified",
        ),
        (
            "environment.image",
            environment.image_compatible,
            "worker image compatibility was not verified",
        ),
    )
    issues.extend(PreflightIssue(code, message) for code, passed, message in checks if not passed)
    return PreflightReport(
        executor=executor_name,
        executor_capabilities=executor_capabilities,
        output_capabilities=sink,
        environment=environment,
        issues=tuple(issues),
    )


def _shared_uri(uri: str) -> bool:
    parsed = urlsplit(uri)
    return bool(
        parsed.scheme
        and parsed.scheme != "file"
        and (parsed.netloc or parsed.scheme == "iceberg")
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _format_matches_uri(artifact: ArtifactRef) -> bool:
    scheme = urlsplit(artifact.uri).scheme.lower()
    if artifact.format == "iceberg":
        return scheme == "iceberg"
    if artifact.format == "postgres":
        return scheme in {"postgres", "postgresql"}
    if artifact.format == "duckdb":
        return scheme == "duckdb"
    return artifact.format == "parquet" and scheme not in {"iceberg", "postgres", "postgresql"}


def report_to_mapping(report: PreflightReport) -> Mapping[str, object]:
    return {
        "executor": report.executor,
        "ready": report.ready,
        "requested_checks": [issue.code for issue in report.issues],
        "issues": [{"code": issue.code, "message": issue.message} for issue in report.issues],
        "executor_capabilities": {
            field: getattr(report.executor_capabilities, field)
            for field in report.executor_capabilities.__dataclass_fields__
        },
        "output_capabilities": {
            field: getattr(report.output_capabilities, field)
            for field in report.output_capabilities.__dataclass_fields__
        },
    }


__all__ = [
    "EnvironmentCapabilities",
    "EnvironmentProbe",
    "PreflightError",
    "PreflightIssue",
    "PreflightReport",
    "UnverifiedEnvironmentProbe",
    "output_capabilities",
    "preflight_request",
    "report_to_mapping",
]
