from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from ballista.artifacts import InMemoryBlobStore
from ballista.config import BallistaProfile
from ballista.probe import CommandResult, KubernetesEnvironmentProbe

from arsenal_core.errors import RetryableError

DIGEST = "sha256:" + "a" * 64
ENV = {
    "KUBECONFIG": "/home/op/.kube/config",
    "ICEBERG_REST_URI": "https://catalog.example",
    "ICEBERG_REST_TOKEN": "s3cr3t-token",
}


def profile(**cluster_overrides: Any) -> BallistaProfile:
    cluster: dict[str, Any] = {
        "context_env": "KUBECONFIG",
        "namespace": "data-jobs",
        "driver_image": f"registry.example/arsenal-spark@{DIGEST}",
        "service_account": "arsenal",
        "master": "k8s://https://cluster.example",
    }
    cluster.update(cluster_overrides)
    return BallistaProfile.model_validate(
        {
            "backend": "spark_kubernetes",
            "cluster": cluster,
            "storage": {
                "staging_uri": "s3://lake/_arsenal/staging",
                "iceberg_catalog": {
                    "type": "rest",
                    "uri_env": "ICEBERG_REST_URI",
                    "token_env": "ICEBERG_REST_TOKEN",
                },
            },
        }
    )


class FakeCluster:
    """Answers kubectl/spark-submit invocations like a healthy cluster unless told otherwise."""

    def __init__(self, *, denied: set[tuple[str, str]] | None = None) -> None:
        self.denied = denied or set()
        self.spark_rc = 0
        self.readyz = "ok"
        self.commands: list[list[str]] = []

    def __call__(
        self, command: Sequence[str], environ: Mapping[str, str], timeout: float
    ) -> CommandResult:
        del environ, timeout
        argv = list(command)
        self.commands.append(argv)
        if argv[0] == "spark-submit":
            return CommandResult(self.spark_rc, "")
        if "--raw" in argv:
            return CommandResult(0 if self.readyz == "ok" else 1, self.readyz + "\n")
        if "can-i" in argv:
            verb, resource = argv[argv.index("can-i") + 1 : argv.index("can-i") + 3]
            allowed = (verb, resource) not in self.denied
            return CommandResult(0 if allowed else 1, "yes\n" if allowed else "no\n")
        raise AssertionError(f"unexpected command {argv}")


def make_probe(
    cluster: FakeCluster,
    *,
    store: Any = None,
    catalog_ok: bool = True,
    environ: Mapping[str, str] = ENV,
) -> KubernetesEnvironmentProbe:
    ids = iter(f"probe-{i}" for i in range(100))
    return KubernetesEnvironmentProbe(
        blob_store=store if store is not None else InMemoryBlobStore(),
        runner=cluster,
        catalog_check=lambda _profile, _env: catalog_ok,
        environ=environ,
        probe_id=lambda: next(ids),
    )


def test_healthy_environment_verifies_every_fact() -> None:
    result = make_probe(FakeCluster()).probe(profile())

    caps = result.capabilities
    assert result.findings == ()
    assert all(getattr(caps, name) for name in caps.__dataclass_fields__)


def test_kubeconfig_from_profile_env_and_driver_service_account_are_used() -> None:
    cluster = FakeCluster()
    make_probe(cluster).probe(profile())

    kubectl = [c for c in cluster.commands if c[0] == "kubectl"]
    assert all(c[1:3] == ["--kubeconfig", "/home/op/.kube/config"] for c in kubectl)
    impersonated = [c for c in kubectl if "--as" in c]
    assert len(impersonated) == 12  # 3 resources x 4 verbs
    assert all(
        c[c.index("--as") + 1] == "system:serviceaccount:data-jobs:arsenal" for c in impersonated
    )


@pytest.mark.parametrize(
    "denied", [("delete", "pods"), ("create", "configmaps"), ("list", "services")]
)
def test_any_missing_driver_permission_fails_rbac(denied: tuple[str, str]) -> None:
    result = make_probe(FakeCluster(denied={denied})).probe(profile())

    assert result.capabilities.rbac_create_get_list_delete is False
    assert result.capabilities.cluster_reachable is True


def test_unreachable_cluster_skips_rbac_and_reports_both() -> None:
    cluster = FakeCluster()
    cluster.readyz = "unhealthy"

    result = make_probe(cluster).probe(profile())

    assert result.capabilities.cluster_reachable is False
    assert result.capabilities.rbac_create_get_list_delete is False
    assert not any("can-i" in c for c in cluster.commands)
    assert any("unreachable" in f for f in result.findings)


def test_missing_spark_submit_is_reported() -> None:
    cluster = FakeCluster()
    cluster.spark_rc = 127

    result = make_probe(cluster).probe(profile())

    assert result.capabilities.spark_submit_available is False


def test_staging_probe_writes_a_fresh_key_and_reads_it_back() -> None:
    store = InMemoryBlobStore()
    make_probe(FakeCluster(), store=store).probe(profile())

    assert store.read("s3://lake/_arsenal/staging/_preflight/probe-0.probe") is not None


class _BrokenStore:
    def __init__(self, *, create_result: bool = True, fail_create: bool = False) -> None:
        self.create_result = create_result
        self.fail_create = fail_create

    def create(self, uri: str, content: bytes) -> bool:
        if self.fail_create:
            raise RetryableError("create staging object failed (GenericError)")
        return self.create_result

    def read(self, uri: str) -> bytes | None:
        return b"something else"


def test_staging_write_failure_fails_both_staging_facts() -> None:
    result = make_probe(FakeCluster(), store=_BrokenStore(fail_create=True)).probe(profile())

    assert result.capabilities.staging_write is False
    assert result.capabilities.staging_read is False


def test_store_that_ignores_create_only_semantics_is_not_trusted() -> None:
    result = make_probe(FakeCluster(), store=_BrokenStore(create_result=False)).probe(profile())

    assert result.capabilities.staging_write is False


def test_read_back_mismatch_fails_staging_read_only() -> None:
    result = make_probe(FakeCluster(), store=_BrokenStore()).probe(profile())

    assert result.capabilities.staging_write is True
    assert result.capabilities.staging_read is False


def test_catalog_requires_credentials_in_environment() -> None:
    env = {k: v for k, v in ENV.items() if k != "ICEBERG_REST_TOKEN"}

    result = make_probe(FakeCluster(), environ=env).probe(profile())

    assert result.capabilities.catalog_credentials is False


def test_catalog_check_failure_is_reported() -> None:
    result = make_probe(FakeCluster(), catalog_ok=False).probe(profile())

    assert result.capabilities.catalog_credentials is False


def test_profile_without_catalog_has_nothing_to_authenticate() -> None:
    data = profile().model_dump()
    data["storage"]["iceberg_catalog"] = None
    data["storage"]["transactional_database"] = True

    result = make_probe(FakeCluster(), catalog_ok=False).probe(BallistaProfile.model_validate(data))

    assert result.capabilities.catalog_credentials is True


def test_mutable_image_tag_requires_explicit_opt_in() -> None:
    tagged = profile(driver_image="registry.example/arsenal-spark:0.4.0")

    assert make_probe(FakeCluster()).probe(tagged).capabilities.image_compatible is False
    opted_in = profile(driver_image="arsenal-spark:dev", allow_mutable_image=True)
    assert make_probe(FakeCluster()).probe(opted_in).capabilities.image_compatible is True


def test_findings_never_contain_secret_values() -> None:
    cluster = FakeCluster(denied={("create", "pods")})
    cluster.spark_rc = 1

    result = make_probe(cluster, store=_BrokenStore(fail_create=True), catalog_ok=False).probe(
        profile(driver_image="x:latest")
    )

    joined = "\n".join(result.findings)
    assert len(result.findings) >= 5
    for secret in ENV.values():
        assert secret not in joined
