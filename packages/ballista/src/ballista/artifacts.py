"""Immutable run manifests and output proofs bound by canonical content hashes."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from arsenal_core.errors import FatalError
from arsenal_core.execution import (
    ArtifactRef,
    ExecutionRequest,
    GuaranteeRequest,
    LeaseToken,
    OutputProof,
    RunContext,
    canonical_hash,
    canonical_json,
)
from ballista.plan import plan_hash, require_portable_mapping

MANIFEST_SCHEMA_VERSION = 1
PROOF_SCHEMA_VERSION = 1
_ARTIFACT_FORMATS = frozenset({"parquet", "iceberg", "duckdb", "postgres"})


class ArtifactError(FatalError):
    """Base error for malformed or conflicting audit artifacts."""


class ImmutableArtifactConflict(ArtifactError):
    """A create-only artifact URI already contains different bytes."""


class ArtifactNotFound(ArtifactError):
    """A required manifest or proof is absent."""


class BlobStore(Protocol):
    """Minimal object-store contract; ``create`` must be conditional/atomic."""

    def create(self, uri: str, content: bytes) -> bool:
        """Create only if absent, returning ``False`` when the key already exists."""

        ...

    def read(self, uri: str) -> bytes | None: ...


class InMemoryBlobStore:
    """Thread-safe create-only store for tests and embedded lifecycle simulations."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def create(self, uri: str, content: bytes) -> bool:
        with self._lock:
            if uri in self._objects:
                return False
            self._objects[uri] = bytes(content)
            return True

    def read(self, uri: str) -> bytes | None:
        with self._lock:
            content = self._objects.get(uri)
            return None if content is None else bytes(content)


@dataclass(frozen=True)
class RunManifest:
    request: ExecutionRequest
    attempt: int
    created_at: datetime
    schema_version: int = MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ArtifactError(f"unsupported run manifest schema: {self.schema_version}")
        if self.attempt < 0:
            raise ArtifactError("run manifest attempt must be non-negative")
        if self.created_at.tzinfo is None:
            raise ArtifactError("run manifest timestamp must be timezone-aware")

    @property
    def content_hash(self) -> str:
        return canonical_hash(manifest_to_mapping(self))


@dataclass(frozen=True)
class VerifiedOutputProof:
    proof: OutputProof
    manifest_hash: str


class ArtifactRepository:
    """Names and verifies immutable audit objects under one remote staging URI."""

    def __init__(self, staging_uri: str, store: BlobStore) -> None:
        parsed = urlsplit(staging_uri)
        if (
            not parsed.scheme
            or parsed.scheme == "file"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Ballista staging must be a shared remote URI")
        self.staging_uri = staging_uri.rstrip("/")
        self._store = store

    def manifest_uri(self, run_id: str, attempt: int) -> str:
        return f"{self.staging_uri}/runs/{_path_component(run_id)}/attempt-{attempt}/manifest.json"

    def proof_uri(self, run_id: str, attempt: int) -> str:
        return f"{self.staging_uri}/runs/{_path_component(run_id)}/attempt-{attempt}/proof.json"

    def write_manifest(self, manifest: RunManifest) -> str:
        uri = self.manifest_uri(manifest.request.context.run_id, manifest.attempt)
        content = manifest_to_json(manifest).encode("utf-8")
        _create_idempotently(self._store, uri, content)
        return uri

    def read_manifest(self, run_id: str, attempt: int) -> RunManifest:
        uri = self.manifest_uri(run_id, attempt)
        content = self._store.read(uri)
        if content is None:
            raise ArtifactNotFound(f"run manifest not found for {run_id!r} attempt {attempt}")
        manifest = manifest_from_json(_decode_utf8(content, "run manifest"))
        if manifest.request.context.run_id != run_id or manifest.attempt != attempt:
            raise ArtifactError("run manifest identity does not match its immutable URI")
        return manifest

    def write_output_proof(self, manifest: RunManifest, proof: OutputProof) -> str:
        manifest_uri = self.manifest_uri(manifest.request.context.run_id, manifest.attempt)
        stored_manifest = self._store.read(manifest_uri)
        if stored_manifest is None:
            raise ArtifactNotFound("cannot publish output proof before its run manifest")
        if stored_manifest != manifest_to_json(manifest).encode("utf-8"):
            raise ImmutableArtifactConflict("stored run manifest differs from proof binding")
        _validate_output_proof(manifest, proof)
        envelope = {
            "schema_version": PROOF_SCHEMA_VERSION,
            "manifest_hash": manifest.content_hash,
            "proof": output_proof_to_mapping(proof),
        }
        uri = self.proof_uri(proof.run_id, manifest.attempt)
        _create_idempotently(self._store, uri, canonical_json(envelope).encode("utf-8"))
        return uri

    def read_output_proof(self, manifest: RunManifest) -> VerifiedOutputProof | None:
        uri = self.proof_uri(manifest.request.context.run_id, manifest.attempt)
        content = self._store.read(uri)
        if content is None:
            return None
        envelope = _json_mapping(_decode_utf8(content, "output proof"), "output proof")
        _exact_keys(envelope, {"schema_version", "manifest_hash", "proof"}, "output proof")
        if envelope.get("schema_version") != PROOF_SCHEMA_VERSION:
            raise ArtifactError("unsupported output proof schema")
        manifest_hash = _text(envelope.get("manifest_hash"), "output proof.manifest_hash")
        if manifest_hash != manifest.content_hash:
            raise ArtifactError("output proof is bound to a different run manifest")
        proof = output_proof_from_mapping(_mapping(envelope.get("proof"), "output proof.proof"))
        _validate_output_proof(manifest, proof)
        return VerifiedOutputProof(proof=proof, manifest_hash=manifest_hash)


def manifest_to_mapping(manifest: RunManifest) -> dict[str, object]:
    request = manifest.request
    return {
        "schema_version": manifest.schema_version,
        "attempt": manifest.attempt,
        "created_at": manifest.created_at,
        "request": {
            "context": {
                "run_id": request.context.run_id,
                "pipeline": request.context.pipeline,
                "revision": request.context.revision,
                "started_at": request.context.started_at,
                "lease": {
                    "resource": request.context.lease.resource,
                    "owner_id": request.context.lease.owner_id,
                    "fence": request.context.lease.fence,
                    "expires_at": request.context.lease.expires_at,
                },
            },
            "plan": dict(request.plan),
            "inputs": [_artifact_to_mapping(item) for item in request.inputs],
            "output": _artifact_to_mapping(request.output),
            "guarantees": {
                "result_exactly_once": request.guarantees.result_exactly_once,
                "atomic_publish": request.guarantees.atomic_publish,
                "delete_apply": request.guarantees.delete_apply,
                "watermark": request.guarantees.watermark,
            },
        },
    }


def manifest_to_json(manifest: RunManifest) -> str:
    return canonical_json(manifest_to_mapping(manifest))


def manifest_from_json(content: str) -> RunManifest:
    root = _json_mapping(content, "run manifest")
    _exact_keys(root, {"schema_version", "attempt", "created_at", "request"}, "run manifest")
    schema_version = _integer(root.get("schema_version"), "run manifest.schema_version")
    attempt = _integer(root.get("attempt"), "run manifest.attempt")
    request = _request_from_mapping(_mapping(root.get("request"), "run manifest.request"))
    return RunManifest(
        request=request,
        attempt=attempt,
        created_at=_timestamp(root.get("created_at"), "run manifest.created_at"),
        schema_version=schema_version,
    )


def output_proof_to_mapping(proof: OutputProof) -> dict[str, object]:
    return {
        "run_id": proof.run_id,
        "revision": proof.revision,
        "output": _artifact_to_mapping(proof.output),
        "input_versions": list(proof.input_versions),
        "row_count": proof.row_count,
        "schema_hash": proof.schema_hash,
        "plan_hash": proof.plan_hash,
        "started_at": proof.started_at,
        "finished_at": proof.finished_at,
        "output_version": proof.output_version,
    }


def output_proof_from_mapping(value: Mapping[str, Any]) -> OutputProof:
    expected = {
        "run_id",
        "revision",
        "output",
        "input_versions",
        "row_count",
        "schema_hash",
        "plan_hash",
        "started_at",
        "finished_at",
        "output_version",
    }
    _exact_keys(value, expected, "output proof.proof")
    raw_versions = value.get("input_versions")
    if not isinstance(raw_versions, list):
        raise ArtifactError("output proof.proof.input_versions must be an array")
    versions = cast(list[object], raw_versions)
    row_count = value.get("row_count")
    if row_count is not None and (
        not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0
    ):
        raise ArtifactError("output proof.proof.row_count must be a non-negative integer or null")
    return OutputProof(
        run_id=_text(value.get("run_id"), "output proof.proof.run_id"),
        revision=_text(value.get("revision"), "output proof.proof.revision"),
        output=_artifact_from_mapping(
            _mapping(value.get("output"), "output proof.proof.output"),
            "output proof.proof.output",
        ),
        input_versions=tuple(
            _text(item, f"output proof.proof.input_versions[{index}]")
            for index, item in enumerate(versions)
        ),
        row_count=row_count,
        schema_hash=_optional_text(value.get("schema_hash"), "output proof.proof.schema_hash"),
        plan_hash=_text(value.get("plan_hash"), "output proof.proof.plan_hash"),
        started_at=_timestamp(value.get("started_at"), "output proof.proof.started_at"),
        finished_at=_timestamp(value.get("finished_at"), "output proof.proof.finished_at"),
        output_version=_optional_text(
            value.get("output_version"), "output proof.proof.output_version"
        ),
    )


def _request_from_mapping(value: Mapping[str, Any]) -> ExecutionRequest:
    _exact_keys(value, {"context", "plan", "inputs", "output", "guarantees"}, "request")
    context = _mapping(value.get("context"), "request.context")
    _exact_keys(context, {"run_id", "pipeline", "revision", "started_at", "lease"}, "context")
    lease = _mapping(context.get("lease"), "request.context.lease")
    _exact_keys(lease, {"resource", "owner_id", "fence", "expires_at"}, "lease")
    fence = _integer(lease.get("fence"), "request.context.lease.fence")
    if fence < 1:
        raise ArtifactError("request.context.lease.fence must be positive")
    run_context = RunContext(
        run_id=_text(context.get("run_id"), "request.context.run_id"),
        pipeline=_text(context.get("pipeline"), "request.context.pipeline"),
        revision=_text(context.get("revision"), "request.context.revision"),
        lease=LeaseToken(
            resource=_text(lease.get("resource"), "request.context.lease.resource"),
            owner_id=_text(lease.get("owner_id"), "request.context.lease.owner_id"),
            fence=fence,
            expires_at=_timestamp(lease.get("expires_at"), "request.context.lease.expires_at"),
        ),
        started_at=_timestamp(context.get("started_at"), "request.context.started_at"),
    )
    raw_plan = _mapping(value.get("plan"), "request.plan")
    require_portable_mapping(raw_plan)
    raw_inputs = value.get("inputs")
    if not isinstance(raw_inputs, list) or not raw_inputs:
        raise ArtifactError("request.inputs must be a non-empty array")
    inputs = cast(list[object], raw_inputs)
    guarantees = _mapping(value.get("guarantees"), "request.guarantees")
    guarantee_fields = {
        "result_exactly_once",
        "atomic_publish",
        "delete_apply",
        "watermark",
    }
    _exact_keys(guarantees, guarantee_fields, "request.guarantees")
    return ExecutionRequest(
        context=run_context,
        plan=dict(raw_plan),
        inputs=tuple(
            _artifact_from_mapping(
                _mapping(item, f"request.inputs[{index}]"), f"request.inputs[{index}]"
            )
            for index, item in enumerate(inputs)
        ),
        output=_artifact_from_mapping(
            _mapping(value.get("output"), "request.output"), "request.output"
        ),
        guarantees=GuaranteeRequest(
            **{
                field: _boolean(guarantees.get(field), f"request.guarantees.{field}")
                for field in guarantee_fields
            }
        ),
    )


def _artifact_to_mapping(artifact: ArtifactRef) -> dict[str, object]:
    return {
        "uri": artifact.uri,
        "format": artifact.format,
        "version": artifact.version,
        "content_hash": artifact.content_hash,
    }


def _artifact_from_mapping(value: Mapping[str, Any], location: str) -> ArtifactRef:
    _exact_keys(value, {"uri", "format", "version", "content_hash"}, location)
    artifact_format = _text(value.get("format"), f"{location}.format")
    if artifact_format not in _ARTIFACT_FORMATS:
        raise ArtifactError(f"{location}.format is unsupported: {artifact_format!r}")
    return ArtifactRef(
        uri=_text(value.get("uri"), f"{location}.uri"),
        format=cast(Any, artifact_format),
        version=_optional_text(value.get("version"), f"{location}.version"),
        content_hash=_optional_text(value.get("content_hash"), f"{location}.content_hash"),
    )


def _validate_output_proof(manifest: RunManifest, proof: OutputProof) -> None:
    request = manifest.request
    expected_versions = tuple(item.version for item in request.inputs)
    if any(version is None for version in expected_versions):
        raise ArtifactError("cannot validate proof for unversioned input")
    if proof.run_id != request.context.run_id:
        raise ArtifactError("output proof run_id does not match manifest")
    if proof.revision != request.context.revision:
        raise ArtifactError("output proof revision does not match manifest")
    if proof.output.uri != request.output.uri or proof.output.format != request.output.format:
        raise ArtifactError("output proof target does not match manifest")
    if proof.input_versions != cast(tuple[str, ...], expected_versions):
        raise ArtifactError("output proof input versions do not match manifest")
    if proof.plan_hash != plan_hash(request.plan):
        raise ArtifactError("output proof plan hash does not match manifest")
    if proof.output_version is None:
        raise ArtifactError("output proof requires an atomic transaction/snapshot version")
    if proof.output.version != proof.output_version:
        raise ArtifactError("output artifact version must equal the committed output version")
    if proof.started_at.tzinfo is None or proof.finished_at.tzinfo is None:
        raise ArtifactError("output proof timestamps must be timezone-aware")
    if proof.finished_at < proof.started_at:
        raise ArtifactError("output proof finished_at precedes started_at")


def _create_idempotently(store: BlobStore, uri: str, content: bytes) -> None:
    if store.create(uri, content):
        return
    existing = store.read(uri)
    if existing != content:
        raise ImmutableArtifactConflict(f"immutable artifact conflict at {uri}")


def _path_component(value: str) -> str:
    if not value or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for character in value
    ):
        raise ValueError("run_id must contain only letters, numbers, '-' and '_'")
    return value


def _json_mapping(content: str, location: str) -> Mapping[str, Any]:
    try:
        value = cast(object, json.loads(content))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ArtifactError(f"{location} is not valid JSON") from exc
    return _mapping(value, location)


def _mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ArtifactError(f"{location} must be an object")
    mapping = cast(Mapping[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        raise ArtifactError(f"{location} must be an object")
    return {cast(str, key): item for key, item in mapping.items()}


def _exact_keys(value: Mapping[str, Any], expected: set[str], location: str) -> None:
    if set(value) != expected:
        raise ArtifactError(f"{location} fields do not match schema")


def _text(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise ArtifactError(f"{location} must be a non-empty string")
    return value


def _optional_text(value: Any, location: str) -> str | None:
    if value is None:
        return None
    return _text(value, location)


def _integer(value: Any, location: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ArtifactError(f"{location} must be an integer")
    return value


def _boolean(value: Any, location: str) -> bool:
    if not isinstance(value, bool):
        raise ArtifactError(f"{location} must be a boolean")
    return value


def _timestamp(value: Any, location: str) -> datetime:
    text = _text(value, location)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ArtifactError(f"{location} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ArtifactError(f"{location} must be timezone-aware")
    return parsed


def _decode_utf8(content: bytes, location: str) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArtifactError(f"{location} must be UTF-8") from exc


__all__ = [
    "ArtifactError",
    "ArtifactNotFound",
    "ArtifactRepository",
    "BlobStore",
    "ImmutableArtifactConflict",
    "InMemoryBlobStore",
    "RunManifest",
    "VerifiedOutputProof",
    "manifest_from_json",
    "manifest_to_json",
    "manifest_to_mapping",
    "output_proof_from_mapping",
    "output_proof_to_mapping",
]
