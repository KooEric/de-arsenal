"""Remote object-store ``BlobStore`` with conditional (create-only) writes.

Manifests and proofs are immutable audit objects. The store therefore has to
make "create if absent" a single server-side decision: S3 ``If-None-Match: *``,
GCS ``ifGenerationMatch=0`` and Azure ``If-None-Match: *`` are all reached
through ``obstore``'s ``mode="create"``. A read-then-write emulation would let
two coordinators both believe they published, so none is offered.

``obstore`` is an optional extra (``de-ballista[object-store]``); the base
install stays free of cloud SDKs. Credentials come from the provider's standard
environment variables (``AWS_*``, ``GOOGLE_*``, ``AZURE_*``) and never pass
through Ballista profiles, logs or error messages.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlsplit

from arsenal_core.errors import FatalError, RetryableError
from arsenal_core.execution import canonical_hash

REMOTE_SCHEMES = frozenset({"s3", "s3a", "gs", "az", "abfs", "abfss"})

_ALREADY_EXISTS = frozenset({"AlreadyExistsError", "PreconditionError"})
_NOT_FOUND = frozenset({"NotFoundError", "FileNotFoundError"})
_ACCESS_DENIED = frozenset({"PermissionDeniedError", "UnauthenticatedError"})
_CONFIGURATION = frozenset(
    {"NotSupportedError", "UnknownConfigurationKeyError", "InvalidPathError"}
)


class BlobStoreError(FatalError):
    """The staging store refused or could not be configured."""


class ObjectStoreBlobStore:
    """``BlobStore`` over one ``obstore`` store rooted at the staging URI.

    Only URIs below ``staging_uri`` are accepted, so a crafted manifest URI
    cannot read or create objects elsewhere in the bucket.
    """

    def __init__(self, staging_uri: str, store: Any) -> None:
        self.staging_uri = staging_uri.rstrip("/")
        self._store = store

    @classmethod
    def from_staging_uri(
        cls,
        staging_uri: str,
        *,
        factory: Callable[[str], Any] | None = None,
    ) -> ObjectStoreBlobStore:
        scheme = urlsplit(staging_uri).scheme.lower()
        if scheme not in REMOTE_SCHEMES:
            raise BlobStoreError(
                f"staging scheme {scheme!r} has no conditional-write object store; "
                f"use one of {sorted(REMOTE_SCHEMES)}"
            )
        build = factory or _default_factory()
        try:
            store = build(staging_uri.rstrip("/"))
        except Exception as exc:  # noqa: BLE001 - provider errors are classified below
            raise _classified(exc, "configure staging store") from None
        return cls(staging_uri, store)

    def create(self, uri: str, content: bytes) -> bool:
        key = self._key(uri)
        obstore = _obstore()
        try:
            obstore.put(self._store, key, bytes(content), mode="create")
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ in _ALREADY_EXISTS:
                return False
            raise _classified(exc, "create staging object") from None
        return True

    def read(self, uri: str) -> bytes | None:
        key = self._key(uri)
        obstore = _obstore()
        try:
            result = obstore.get(self._store, key)
            return bytes(result.bytes())
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ in _NOT_FOUND:
                return None
            raise _classified(exc, "read staging object") from None

    def _key(self, uri: str) -> str:
        prefix = self.staging_uri + "/"
        if not uri.startswith(prefix):
            raise BlobStoreError("object URI is outside the configured staging prefix")
        key = uri[len(prefix) :]
        parts = key.split("/")
        if not key or any(part in {"", ".", ".."} for part in parts):
            raise BlobStoreError("object URI has an empty or relative path component")
        return key


def listing_fingerprint(store: Any, prefix: str = "") -> str:
    """Content fingerprint of every Parquet object under ``prefix``.

    Path, size and ETag identify object bytes on S3/GCS/Azure, so any added,
    removed or rewritten file changes the fingerprint. Used as the immutable
    ``version`` of a Parquet input: the worker recomputes it and refuses to
    run when the dataset moved after planning.
    """

    obstore = _obstore()
    entries: list[tuple[str, int, str]] = []
    try:
        for batch in obstore.list(store, prefix=prefix or None):
            for meta in batch:
                path = str(meta["path"])
                if path.endswith(".parquet"):
                    entries.append((path, int(meta["size"]), str(meta.get("e_tag") or "")))
    except Exception as exc:  # noqa: BLE001
        raise _classified(exc, "list input objects") from None
    if not entries:
        raise BlobStoreError("input prefix contains no Parquet objects")
    return "listing:" + canonical_hash(sorted(entries))


def fingerprint_uri(uri: str, *, factory: Callable[[str], Any] | None = None) -> str:
    """Listing fingerprint for a remote Parquet prefix such as ``s3://lake/orders``."""

    scheme = urlsplit(uri).scheme.lower()
    if scheme not in REMOTE_SCHEMES:
        raise BlobStoreError(f"cannot fingerprint {scheme!r} inputs; use a remote object store URI")
    build = factory or _default_factory()
    try:
        store = build(uri.rstrip("/"))
    except Exception as exc:  # noqa: BLE001
        raise _classified(exc, "configure input store") from None
    return listing_fingerprint(store)


def _classified(exc: Exception, operation: str) -> Exception:
    """Map provider errors to Arsenal's taxonomy without echoing provider text.

    Provider messages can carry presigned URLs, account names or request
    headers; only the error class name is surfaced.
    """

    name = type(exc).__name__
    if name in _ACCESS_DENIED:
        return BlobStoreError(f"{operation}: access denied ({name})")
    if name in _CONFIGURATION or isinstance(exc, ValueError):
        return BlobStoreError(f"{operation}: invalid store configuration ({name})")
    return RetryableError(f"{operation} failed ({name})")


def _obstore() -> Any:
    try:
        import obstore
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise BlobStoreError(
            "remote staging requires the object-store extra: "
            "pip install 'de-ballista[object-store]'"
        ) from exc
    return cast(Any, obstore)


def _default_factory() -> Callable[[str], Any]:
    try:
        from obstore.store import from_url
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise BlobStoreError(
            "remote staging requires the object-store extra: "
            "pip install 'de-ballista[object-store]'"
        ) from exc
    return cast(Callable[[str], Any], from_url)


__all__ = [
    "REMOTE_SCHEMES",
    "BlobStoreError",
    "ObjectStoreBlobStore",
    "fingerprint_uri",
    "listing_fingerprint",
]
