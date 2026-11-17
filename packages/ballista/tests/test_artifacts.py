from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from ballista.artifacts import (
    ArtifactError,
    ArtifactRepository,
    ImmutableArtifactConflict,
    InMemoryBlobStore,
    RunManifest,
    manifest_from_json,
    manifest_to_json,
)
from ballista.plan import plan_hash, plan_to_mapping

from arsenal_core.execution import (
    ArtifactRef,
    ExecutionRequest,
    GuaranteeRequest,
    LeaseToken,
    OutputProof,
    RunContext,
)
from gladius.plan import preflight_portable
from gladius.spec import TransformSpec


def manifest() -> RunManifest:
    now = datetime(2026, 8, 22, tzinfo=UTC)
    spec = TransformSpec.model_validate(
        {
            "name": "daily",
            "input": "s3://lake/input",
            "steps": [{"derive": {"gross": "quantity * price"}}],
            "output": "iceberg://analytics.daily",
        }
    )
    request = ExecutionRequest(
        context=RunContext(
            run_id="run-1",
            pipeline="daily",
            revision="rev-1",
            lease=LeaseToken("run:run-1", "owner", 1, now + timedelta(minutes=5)),
            started_at=now,
        ),
        plan=plan_to_mapping(preflight_portable(spec)),
        inputs=(ArtifactRef("s3://lake/input", "parquet", version="snapshot-1"),),
        output=ArtifactRef("iceberg://analytics.daily", "iceberg"),
        guarantees=GuaranteeRequest(result_exactly_once=True, atomic_publish=True),
    )
    return RunManifest(request=request, attempt=0, created_at=now)


def proof(value: RunManifest) -> OutputProof:
    now = datetime(2026, 8, 22, tzinfo=UTC)
    return OutputProof(
        run_id=value.request.context.run_id,
        revision=value.request.context.revision,
        output=replace(value.request.output, version="iceberg-snapshot-9"),
        input_versions=("snapshot-1",),
        row_count=17,
        schema_hash="schema-hash",
        plan_hash=plan_hash(value.request.plan),
        started_at=now,
        finished_at=now + timedelta(seconds=3),
        output_version="iceberg-snapshot-9",
    )


def test_manifest_round_trip_is_canonical_and_immutable() -> None:
    value = manifest()

    restored = manifest_from_json(manifest_to_json(value))

    assert restored == value
    assert restored.content_hash == value.content_hash


def test_repository_create_is_idempotent_but_conflicts_on_different_manifest() -> None:
    repository = ArtifactRepository("s3://lake/staging", InMemoryBlobStore())
    value = manifest()
    uri = repository.write_manifest(value)
    assert repository.write_manifest(value) == uri

    conflicting = replace(value, created_at=value.created_at + timedelta(seconds=1))
    with pytest.raises(ImmutableArtifactConflict):
        repository.write_manifest(conflicting)


def test_output_proof_is_bound_to_manifest_and_requires_transaction_version() -> None:
    repository = ArtifactRepository("s3://lake/staging", InMemoryBlobStore())
    value = manifest()
    repository.write_manifest(value)
    repository.write_output_proof(value, proof(value))

    verified = repository.read_output_proof(value)

    assert verified is not None
    assert verified.proof.row_count == 17
    assert verified.manifest_hash == value.content_hash

    missing_version = replace(proof(value), output_version=None)
    other = ArtifactRepository("s3://other/staging", InMemoryBlobStore())
    other.write_manifest(value)
    with pytest.raises(ArtifactError, match="transaction/snapshot version"):
        other.write_output_proof(value, missing_version)
