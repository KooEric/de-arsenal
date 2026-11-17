"""Spark driver entrypoint: ``ballista-worker --manifest URI --manifest-hash H``.

Runs inside the worker image, never on the coordinator. It trusts nothing but
the immutable manifest whose hash the coordinator pinned at submit time:

1. read the manifest from the staging store and verify its content hash and
   its deterministic URI;
2. read exactly the recorded input versions, render the portable plan, and
   publish through the Iceberg committer;
3. write the immutable output proof the coordinator's reconcile looks for.

Exit status 0 means the proof exists. Errors print one secret-free line.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable, Sequence
from typing import Any

from arsenal_core.errors import ArsenalError, FatalError
from ballista.artifacts import ArtifactRepository, BlobStore, manifest_from_json
from ballista.iceberg import DEFAULT_SPARK_CATALOG, IcebergOutputCommitter, SnapshotInputReader
from ballista.storage import ObjectStoreBlobStore, fingerprint_uri
from ballista.worker import execute_manifest


def staging_root(manifest_uri: str) -> str:
    marker = manifest_uri.rfind("/runs/")
    if marker <= 0 or not manifest_uri.endswith("/manifest.json"):
        raise FatalError("manifest URI does not follow the Ballista staging layout")
    return manifest_uri[:marker]


def run_worker(
    manifest_uri: str,
    manifest_hash: str,
    *,
    store: BlobStore,
    spark: Any,
    functions: Any,
    fingerprint: Callable[[str], str] | None = None,
    catalog: str = DEFAULT_SPARK_CATALOG,
) -> str:
    """Execute one manifest and return the URI of its published proof."""

    content = store.read(manifest_uri)
    if content is None:
        raise FatalError("run manifest is missing from staging")
    manifest = manifest_from_json(content.decode("utf-8"))
    if manifest.content_hash != manifest_hash:
        raise FatalError("run manifest hash does not match the submitted hash")
    repository = ArtifactRepository(staging_root(manifest_uri), store)
    expected = repository.manifest_uri(manifest.request.context.run_id, manifest.attempt)
    if expected != manifest_uri:
        raise FatalError("run manifest identity does not match its URI")
    proof = execute_manifest(
        manifest,
        spark=spark,
        functions=functions,
        reader=SnapshotInputReader(catalog=catalog, fingerprint=fingerprint),
        committer=IcebergOutputCommitter(spark, functions, catalog=catalog),
    )
    return repository.write_output_proof(manifest, proof)


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - needs a Spark image
    parser = argparse.ArgumentParser(prog="ballista-worker")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--manifest-hash", required=True)
    parser.add_argument("--catalog", default=DEFAULT_SPARK_CATALOG)
    args = parser.parse_args(argv)
    try:
        pyspark_sql: Any = importlib.import_module("pyspark.sql")
        spark_functions: Any = importlib.import_module("pyspark.sql.functions")

        spark = pyspark_sql.SparkSession.builder.getOrCreate()
        proof_uri = run_worker(
            args.manifest,
            args.manifest_hash,
            store=ObjectStoreBlobStore.from_staging_uri(staging_root(args.manifest)),
            spark=spark,
            functions=spark_functions,
            fingerprint=fingerprint_uri,
            catalog=args.catalog,
        )
    except ArsenalError as exc:
        print(f"ballista-worker: {exc}", file=sys.stderr)
        return 1
    print(f"ballista-worker: published {proof_uri}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
