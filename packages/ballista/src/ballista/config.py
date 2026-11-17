"""Strict executor profiles that contain references to secrets, never secret values."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from arsenal_core.errors import FatalError

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MEMORY = re.compile(r"^[1-9][0-9]*(?:[kKmMgGtT])$")
_SPARK_CONF_PREFIXES = ("spark.executor.", "spark.sql.", "spark.driver.", "spark.kubernetes.")
_FORBIDDEN_SPARK_CONF = frozenset(
    {
        "spark.jars.packages",
        "spark.submit.pyFiles",
        "spark.driver.extraJavaOptions",
        "spark.executor.extraJavaOptions",
        "spark.driver.extraClassPath",
        "spark.executor.extraClassPath",
    }
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _validate_env_name(value: str) -> str:
    if not _ENV_NAME.fullmatch(value):
        raise ValueError("must be an environment variable name")
    return value


def _validate_optional_env_name(value: str | None) -> str | None:
    return None if value is None else _validate_env_name(value)


class ClusterProfile(_FrozenModel):
    context_env: str
    namespace: str = Field(min_length=1)
    driver_image: str = Field(min_length=1)
    service_account: str = Field(min_length=1)
    master: str | None = None
    master_env: str | None = None

    _context_env_name = field_validator("context_env")(_validate_env_name)
    _master_env_name = field_validator("master_env")(_validate_optional_env_name)

    @model_validator(mode="after")
    def _one_master_source(self) -> ClusterProfile:
        if self.master is not None and self.master_env is not None:
            raise ValueError("configure either cluster.master or cluster.master_env, not both")
        if self.master is not None and not self.master.startswith("k8s://"):
            raise ValueError("cluster.master must start with k8s://")
        return self


class IcebergCatalogProfile(_FrozenModel):
    type: Literal["rest"]
    uri_env: str
    token_env: str | None = None

    _uri_env_name = field_validator("uri_env")(_validate_env_name)
    _token_env_name = field_validator("token_env")(_validate_optional_env_name)


class StorageProfile(_FrozenModel):
    staging_uri: str = Field(min_length=1)
    iceberg_catalog: IcebergCatalogProfile | None = None
    transactional_database: bool = False

    @field_validator("staging_uri")
    @classmethod
    def _remote_staging_uri(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            not parsed.scheme
            or parsed.scheme == "file"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("staging_uri must be a shared remote URI, not a local path")
        return value.rstrip("/")


class SparkSettings(_FrozenModel):
    executor_instances: int = Field(default=1, ge=1)
    executor_cores: int = Field(default=1, ge=1)
    executor_memory: str = "2g"
    conf: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @field_validator("executor_memory")
    @classmethod
    def _memory_shape(cls, value: str) -> str:
        if not _MEMORY.fullmatch(value):
            raise ValueError("executor_memory must look like 512m, 4g, or 1t")
        return value.lower()

    @field_validator("conf")
    @classmethod
    def _safe_conf(
        cls, value: dict[str, str | int | float | bool]
    ) -> dict[str, str | int | float | bool]:
        for key, item in value.items():
            lowered = key.lower()
            if not key.startswith(_SPARK_CONF_PREFIXES):
                raise ValueError(f"Spark configuration key {key!r} is not allowlisted")
            if key in _FORBIDDEN_SPARK_CONF or any(
                marker in lowered for marker in ("secret", "token", "password", "credential")
            ):
                raise ValueError(f"Spark configuration key {key!r} is not permitted")
            if isinstance(item, str) and ("${" in item or "-----BEGIN" in item):
                raise ValueError(f"Spark configuration value for {key!r} must not contain secrets")
        return value


class BallistaProfile(_FrozenModel):
    backend: Literal["spark_kubernetes"]
    cluster: ClusterProfile
    storage: StorageProfile
    spark: SparkSettings = SparkSettings()

    def required_environment(self) -> tuple[str, ...]:
        names = [self.cluster.context_env]
        if self.cluster.master_env is not None:
            names.append(self.cluster.master_env)
        if self.storage.iceberg_catalog is not None:
            names.append(self.storage.iceberg_catalog.uri_env)
            if self.storage.iceberg_catalog.token_env is not None:
                names.append(self.storage.iceberg_catalog.token_env)
        return tuple(names)

    def missing_environment(self, environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
        source = os.environ if environ is None else environ
        return tuple(name for name in self.required_environment() if not source.get(name))

    def master_url(self, environ: Mapping[str, str] | None = None) -> str | None:
        if self.cluster.master is not None:
            return self.cluster.master
        if self.cluster.master_env is None:
            return None
        source = os.environ if environ is None else environ
        value = source.get(self.cluster.master_env)
        return value if value and value.startswith("k8s://") else None


def load_profile(path: Path) -> BallistaProfile:
    """Load a profile without interpolating or echoing runtime secret values."""

    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FatalError(f"profile file not found: {path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise FatalError(f"cannot read profile {path}: {type(exc).__name__}") from exc
    if not isinstance(raw, dict):
        raise FatalError(f"invalid Ballista profile {path}: expected a YAML mapping")
    try:
        return BallistaProfile.model_validate(raw)
    except ValidationError as exc:
        lines = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_input=False)
        ]
        raise FatalError(f"invalid Ballista profile {path}:\n  " + "\n  ".join(lines)) from exc


__all__ = [
    "BallistaProfile",
    "ClusterProfile",
    "IcebergCatalogProfile",
    "SparkSettings",
    "StorageProfile",
    "load_profile",
]
