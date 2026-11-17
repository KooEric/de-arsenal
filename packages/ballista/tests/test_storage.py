from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from ballista.storage import BlobStoreError, ObjectStoreBlobStore

from arsenal_core.errors import RetryableError

obstore_store = pytest.importorskip("obstore.store")

STAGING = "s3://bucket/ballista"


def memory_blob_store() -> ObjectStoreBlobStore:
    return ObjectStoreBlobStore(STAGING, obstore_store.MemoryStore())


def test_create_is_conditional_and_read_round_trips() -> None:
    store = memory_blob_store()
    uri = f"{STAGING}/runs/r1/attempt-0/manifest.json"

    assert store.create(uri, b"first") is True
    assert store.create(uri, b"second") is False
    assert store.read(uri) == b"first"


def test_read_missing_object_returns_none() -> None:
    assert memory_blob_store().read(f"{STAGING}/runs/none/manifest.json") is None


@pytest.mark.parametrize(
    "uri",
    [
        "s3://bucket/elsewhere/manifest.json",
        "s3://bucket/ballista-evil/manifest.json",
        f"{STAGING}/runs/../../escape.json",
        f"{STAGING}/runs//double.json",
        f"{STAGING}/",
    ],
)
def test_uris_outside_staging_or_relative_are_rejected(uri: str) -> None:
    store = memory_blob_store()
    with pytest.raises(BlobStoreError):
        store.create(uri, b"x")
    with pytest.raises(BlobStoreError):
        store.read(uri)


def test_concurrent_creates_have_exactly_one_winner(tmp_path: Path) -> None:
    store = ObjectStoreBlobStore(STAGING, obstore_store.LocalStore(prefix=tmp_path))
    uri = f"{STAGING}/runs/race/attempt-0/proof.json"
    results: list[bool] = []
    barrier = threading.Barrier(8)

    def contend(index: int) -> None:
        barrier.wait()
        results.append(store.create(uri, f"writer-{index}".encode()))

    threads = [threading.Thread(target=contend, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(True) == 1
    content = store.read(uri)
    assert content is not None and content.startswith(b"writer-")


class _FakeError(Exception):
    pass


def _named(name: str) -> type[Exception]:
    return type(name, (_FakeError,), {})


class _RaisingStore:
    """Stands in for an obstore store; ``obstore.put`` forwards to it via monkeypatch."""


@pytest.mark.parametrize(
    ("error_name", "expected"),
    [
        ("PermissionDeniedError", BlobStoreError),
        ("UnauthenticatedError", BlobStoreError),
        ("NotSupportedError", BlobStoreError),
        ("GenericError", RetryableError),
    ],
)
def test_provider_errors_are_classified_without_leaking_text(
    monkeypatch: pytest.MonkeyPatch, error_name: str, expected: type[Exception]
) -> None:
    import obstore

    secret_text = "https://bucket.s3.amazonaws.com/x?X-Amz-Signature=SECRET"

    def boom(*args: Any, **kwargs: Any) -> None:
        raise _named(error_name)(secret_text)

    monkeypatch.setattr(obstore, "put", boom)
    monkeypatch.setattr(obstore, "get", boom)
    store = ObjectStoreBlobStore(STAGING, _RaisingStore())

    for call in (
        lambda: store.create(f"{STAGING}/a.json", b"x"),
        lambda: store.read(f"{STAGING}/a.json"),
    ):
        with pytest.raises(expected) as caught:
            call()
        assert "SECRET" not in str(caught.value)
        assert error_name in str(caught.value)


@pytest.mark.parametrize("uri", ["file:///tmp/staging", "memory:///", "https://host/path"])
def test_factory_refuses_stores_without_conditional_writes(uri: str) -> None:
    with pytest.raises(BlobStoreError, match="conditional-write"):
        ObjectStoreBlobStore.from_staging_uri(uri)


def test_factory_builds_store_rooted_at_staging_uri() -> None:
    seen: list[str] = []

    def factory(url: str) -> Any:
        seen.append(url)
        return obstore_store.MemoryStore()

    store = ObjectStoreBlobStore.from_staging_uri("gs://bucket/stage/", factory=factory)

    assert seen == ["gs://bucket/stage"]
    assert store.create("gs://bucket/stage/runs/x.json", b"1") is True


def test_factory_configuration_errors_do_not_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(url: str) -> Any:
        raise ValueError(f"bad endpoint for {url} token=SECRET")

    with pytest.raises(BlobStoreError) as caught:
        ObjectStoreBlobStore.from_staging_uri(STAGING, factory=factory)
    assert "SECRET" not in str(caught.value)


def test_artifact_repository_publishes_manifest_and_proof_through_object_store() -> None:
    from ballista.artifacts import ArtifactRepository

    samples = _load_sibling("test_artifacts")
    manifest, proof = samples.manifest, samples.proof
    repository = ArtifactRepository("s3://lake/staging", memory_blob_store_at("s3://lake/staging"))
    value = manifest()

    uri = repository.write_manifest(value)
    assert repository.write_manifest(value) == uri  # idempotent re-publish
    assert repository.read_manifest(value.request.context.run_id, value.attempt) == value

    repository.write_output_proof(value, proof(value))
    verified = repository.read_output_proof(value)
    assert verified is not None and verified.manifest_hash == value.content_hash


def memory_blob_store_at(staging: str) -> ObjectStoreBlobStore:
    return ObjectStoreBlobStore(staging, obstore_store.MemoryStore())


def _load_sibling(name: str) -> Any:
    """Reuse fixtures from a sibling test module under ``--import-mode=importlib``."""
    import importlib.util

    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"ballista_tests_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_listing_fingerprint_changes_when_any_parquet_object_changes() -> None:
    import obstore
    from ballista.storage import listing_fingerprint

    store = obstore_store.MemoryStore()
    obstore.put(store, "d/a.parquet", b"1")
    obstore.put(store, "d/_SUCCESS", b"")
    first = listing_fingerprint(store, "d")
    assert listing_fingerprint(store, "d") == first  # deterministic

    obstore.put(store, "d/_SUCCESS", b"marker")  # non-parquet noise is ignored
    assert listing_fingerprint(store, "d") == first

    obstore.put(store, "d/b.parquet", b"22")
    second = listing_fingerprint(store, "d")
    assert second != first

    obstore.put(store, "d/b.parquet", b"23")  # rewrite in place
    assert listing_fingerprint(store, "d") != second


def test_listing_fingerprint_refuses_empty_dataset() -> None:
    from ballista.storage import listing_fingerprint

    with pytest.raises(BlobStoreError, match="no Parquet"):
        listing_fingerprint(obstore_store.MemoryStore(), "missing")
