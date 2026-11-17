from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from ballista.artifacts import ArtifactRepository, InMemoryBlobStore, manifest_to_json
from ballista.worker_main import run_worker, staging_root

from arsenal_core.errors import FatalError

STAGING = "s3://lake/staging"


def _sibling(name: str) -> Any:
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"ballista_tests_wm_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> Any:
    iceberg_tests = _sibling("test_iceberg")
    session = iceberg_tests.FakeSpark()

    class Reader:
        def read(self, spark: Any, inputs: Any) -> Any:
            return iceberg_tests.FakeFrame(session, [(1.0,), (2.0,)])

    import ballista.worker as worker
    import ballista.worker_main as worker_main

    def passthrough(frame: Any, *_args: Any) -> Any:
        return frame

    def reader_factory(**_kwargs: Any) -> Reader:
        return Reader()

    monkeypatch.setattr(worker, "apply_spark_plan", passthrough)
    monkeypatch.setattr(worker_main, "SnapshotInputReader", reader_factory)
    return iceberg_tests, session


def test_worker_publishes_proof_that_reconcile_can_verify(fakes: Any) -> None:
    iceberg_tests, spark = fakes
    store = InMemoryBlobStore()
    repository = ArtifactRepository(STAGING, store)
    manifest = _sibling("test_artifacts").manifest()
    manifest_uri = repository.write_manifest(manifest)

    proof_uri = run_worker(
        manifest_uri,
        manifest.content_hash,
        store=store,
        spark=spark,
        functions=iceberg_tests.FakeFunctions(),
    )

    assert proof_uri == repository.proof_uri("run-1", 0)
    verified = repository.read_output_proof(manifest)
    assert verified is not None and verified.proof.row_count == 2


def test_worker_rejects_manifest_whose_hash_was_not_pinned(fakes: Any) -> None:
    iceberg_tests, spark = fakes
    store = InMemoryBlobStore()
    manifest = _sibling("test_artifacts").manifest()
    uri = ArtifactRepository(STAGING, store).write_manifest(manifest)

    with pytest.raises(FatalError, match="hash"):
        run_worker(uri, "0" * 64, store=store, spark=spark, functions=iceberg_tests.FakeFunctions())
    assert spark.tables == {}


def test_worker_rejects_manifest_copied_to_another_run_path(fakes: Any) -> None:
    iceberg_tests, spark = fakes
    store = InMemoryBlobStore()
    manifest = _sibling("test_artifacts").manifest()
    foreign = f"{STAGING}/runs/other-run/attempt-0/manifest.json"
    store.create(foreign, manifest_to_json(manifest).encode())

    with pytest.raises(FatalError, match="identity"):
        run_worker(
            foreign,
            manifest.content_hash,
            store=store,
            spark=spark,
            functions=iceberg_tests.FakeFunctions(),
        )


def test_missing_manifest_fails_closed(fakes: Any) -> None:
    iceberg_tests, spark = fakes

    with pytest.raises(FatalError, match="missing"):
        run_worker(
            f"{STAGING}/runs/x/attempt-0/manifest.json",
            "h",
            store=InMemoryBlobStore(),
            spark=spark,
            functions=iceberg_tests.FakeFunctions(),
        )


@pytest.mark.parametrize(
    ("uri", "root"),
    [
        ("s3://lake/staging/runs/r/attempt-0/manifest.json", "s3://lake/staging"),
        ("s3://lake/runs/staging/runs/r/attempt-2/manifest.json", "s3://lake/runs/staging"),
    ],
)
def test_staging_root_is_derived_from_layout(uri: str, root: str) -> None:
    assert staging_root(uri) == root


@pytest.mark.parametrize("uri", ["s3://lake/staging/manifest.json", "s3://lake/runs/r/proof.json"])
def test_staging_root_rejects_foreign_layouts(uri: str) -> None:
    with pytest.raises(FatalError):
        staging_root(uri)
