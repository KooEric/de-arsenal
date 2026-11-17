"""Concrete remote-environment probe for Spark on Kubernetes.

Every ``EnvironmentCapabilities`` fact is established by a real check against
the configured cluster and staging store; nothing defaults to ``True``. Each
failed check yields a short, secret-free finding so ``ballista plan`` can say
*why* a run is not ready. Process output is never echoed: kubectl and
spark-submit diagnostics can contain cluster endpoints and token paths.
"""

from __future__ import annotations

import importlib
import os
import re
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from arsenal_core.errors import ArsenalError
from ballista.artifacts import BlobStore
from ballista.config import BallistaProfile
from ballista.preflight import EnvironmentCapabilities

CommandRunner = Callable[[Sequence[str], Mapping[str, str], float], "CommandResult"]
CatalogCheck = Callable[[BallistaProfile, Mapping[str, str]], bool]

_DIGEST_PINNED = re.compile(r"@sha256:[0-9a-f]{64}$")
# Spark's driver creates executor pods and their headless service/config map.
_DRIVER_RESOURCES = ("pods", "services", "configmaps")
_DRIVER_VERBS = ("create", "get", "list", "delete")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str


@dataclass(frozen=True)
class ProbeResult:
    capabilities: EnvironmentCapabilities
    findings: tuple[str, ...]


def subprocess_runner(
    command: Sequence[str], environ: Mapping[str, str], timeout: float
) -> CommandResult:
    """Shell-free runner; a missing binary or timeout is a failed check, not a crash."""

    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            env=dict(environ),
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return CommandResult(returncode=127, stdout="")
    return CommandResult(returncode=completed.returncode, stdout=completed.stdout)


class KubernetesEnvironmentProbe:
    def __init__(
        self,
        *,
        blob_store: BlobStore,
        spark_submit: str = "spark-submit",
        kubectl: str = "kubectl",
        runner: CommandRunner = subprocess_runner,
        catalog_check: CatalogCheck | None = None,
        environ: Mapping[str, str] | None = None,
        probe_id: Callable[[], str] = lambda: uuid.uuid4().hex,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._blob_store = blob_store
        self._spark_submit = spark_submit
        self._kubectl = kubectl
        self._runner = runner
        self._catalog_check = catalog_check or iceberg_rest_catalog_check
        self._environ = dict(os.environ if environ is None else environ)
        self._probe_id = probe_id
        self._timeout = timeout_seconds

    def inspect(self, profile: BallistaProfile) -> EnvironmentCapabilities:
        return self.probe(profile).capabilities

    def probe(self, profile: BallistaProfile) -> ProbeResult:
        findings: list[str] = []
        spark_ok = self._check(
            findings, "spark-submit --version failed or binary missing", self._spark_submit_ok
        )
        cluster_ok = self._check(
            findings, "Kubernetes API /readyz is not ok", lambda: self._cluster_ok(profile)
        )
        # RBAC answers are meaningless against an unreachable API server.
        rbac_ok = cluster_ok and self._check(
            findings,
            "service account lacks create/get/list/delete on pods/services/configmaps",
            lambda: self._rbac_ok(profile),
        )
        if not cluster_ok:
            findings.append("RBAC not checked because the cluster is unreachable")
        staging_write, staging_read = self._staging(profile, findings)
        catalog_ok = self._check(
            findings,
            "Iceberg catalog credentials missing or catalog unreachable",
            lambda: self._catalog_ok(profile),
        )
        image_ok = self._check(
            findings,
            "driver_image is not digest-pinned (@sha256:...) and "
            "cluster.allow_mutable_image is false",
            lambda: _image_ok(profile),
        )
        return ProbeResult(
            capabilities=EnvironmentCapabilities(
                spark_submit_available=spark_ok,
                cluster_reachable=cluster_ok,
                rbac_create_get_list_delete=rbac_ok,
                staging_read=staging_read,
                staging_write=staging_write,
                catalog_credentials=catalog_ok,
                image_compatible=image_ok,
            ),
            findings=tuple(findings),
        )

    def _check(self, findings: list[str], failure: str, check: Callable[[], bool]) -> bool:
        try:
            passed = check()
        except ArsenalError:
            passed = False
        if not passed:
            findings.append(failure)
        return passed

    def _run(self, command: Sequence[str]) -> CommandResult:
        return self._runner(command, self._environ, self._timeout)

    def _kubectl_base(self, profile: BallistaProfile) -> list[str]:
        command = [self._kubectl]
        kubeconfig = self._environ.get(profile.cluster.context_env)
        if kubeconfig:
            command.extend(["--kubeconfig", kubeconfig])
        return command

    def _spark_submit_ok(self) -> bool:
        return self._run([self._spark_submit, "--version"]).returncode == 0

    def _cluster_ok(self, profile: BallistaProfile) -> bool:
        result = self._run(
            [*self._kubectl_base(profile), "get", "--raw", "/readyz", "--request-timeout=10s"]
        )
        return result.returncode == 0 and result.stdout.strip() == "ok"

    def _rbac_ok(self, profile: BallistaProfile) -> bool:
        namespace = profile.cluster.namespace
        service_account = f"system:serviceaccount:{namespace}:{profile.cluster.service_account}"
        base = self._kubectl_base(profile)
        # The submitter must create the driver pod; the driver's service
        # account must manage executors.
        checks = [[*base, "auth", "can-i", "create", "pods", "--namespace", namespace]]
        checks.extend(
            [
                *base,
                "auth",
                "can-i",
                verb,
                resource,
                "--namespace",
                namespace,
                "--as",
                service_account,
            ]
            for resource in _DRIVER_RESOURCES
            for verb in _DRIVER_VERBS
        )
        return all(self._can_i(command) for command in checks)

    def _can_i(self, command: Sequence[str]) -> bool:
        result = self._run(command)
        return result.returncode == 0 and result.stdout.strip().lower() == "yes"

    def _staging(self, profile: BallistaProfile, findings: list[str]) -> tuple[bool, bool]:
        uri = f"{profile.storage.staging_uri}/_preflight/{self._probe_id()}.probe"
        content = b"de-arsenal ballista preflight\n"
        try:
            created = self._blob_store.create(uri, content)
        except ArsenalError:
            findings.append("staging conditional write failed")
            return False, False
        if not created:
            # A fresh random key must not exist; if it does, the store is not
            # honouring create-only semantics.
            findings.append("staging store reported an existing object for a fresh key")
            return False, False
        try:
            read_back = self._blob_store.read(uri)
        except ArsenalError:
            findings.append("staging read failed")
            return True, False
        if read_back != content:
            findings.append("staging read returned different bytes than were written")
            return True, False
        return True, True

    def _catalog_ok(self, profile: BallistaProfile) -> bool:
        catalog = profile.storage.iceberg_catalog
        if catalog is None:
            # Nothing to authenticate; output preflight separately refuses
            # Iceberg outputs without a configured catalog.
            return True
        required = [catalog.uri_env] + ([catalog.token_env] if catalog.token_env else [])
        if any(not self._environ.get(name) for name in required):
            return False
        return self._catalog_check(profile, self._environ)


def iceberg_rest_catalog_check(profile: BallistaProfile, environ: Mapping[str, str]) -> bool:
    """Authenticate to the REST catalog and list namespaces (read-only)."""

    catalog = profile.storage.iceberg_catalog
    if catalog is None:
        return True
    try:
        pyiceberg_catalog: Any = importlib.import_module("pyiceberg.catalog")
    except ImportError:
        return False
    properties: dict[str, str] = {"type": "rest", "uri": environ[catalog.uri_env]}
    if catalog.token_env:
        properties["token"] = environ[catalog.token_env]
    try:
        pyiceberg_catalog.load_catalog("ballista-preflight", **properties).list_namespaces()
    except Exception:  # noqa: BLE001 - any failure means "not verified"
        return False
    return True


def _image_ok(profile: BallistaProfile) -> bool:
    if profile.cluster.allow_mutable_image:
        return True
    return bool(_DIGEST_PINNED.search(profile.cluster.driver_image))


__all__ = [
    "CommandResult",
    "KubernetesEnvironmentProbe",
    "ProbeResult",
    "iceberg_rest_catalog_check",
    "subprocess_runner",
]
