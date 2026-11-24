"""The base `de-arsenal` install must stay light (P2 release checklist).

Walks the installed dependency closure of ``de-arsenal`` — required
dependencies only, extras excluded — and fails if any heavy runtime that the
P2 tools use optionally (Spark, Kubernetes, Iceberg, Kafka, object-store
clients, Postgres drivers) or the P2 packages themselves would be pulled in.
"""

from __future__ import annotations

import re
from importlib import metadata

HEAVY = {
    "pyspark",
    "kubernetes",
    "pyiceberg",
    "kafka-python",
    "confluent-kafka",
    "aiokafka",
    "obstore",
    "psycopg",
    "psycopg-binary",
    "redshift-connector",
    "boto3",
    "dbt-duckdb",
    "de-hasta",
    "de-ballista",
    "de-aquila",
    "de-pilum",
}

_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _required(dist: str) -> list[str]:
    names: list[str] = []
    for requirement in metadata.requires(dist) or []:
        _, _, marker = requirement.partition(";")
        if "extra" in marker:
            continue
        match = _NAME.match(requirement)
        if match:
            names.append(_normalize(match.group(1)))
    return names


def base_closure(root: str) -> set[str]:
    seen: set[str] = set()
    pending = [_normalize(root)]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            pending.extend(_required(name))
        except metadata.PackageNotFoundError:
            continue  # environment-marker dependency not installed on this platform
    return seen


def test_base_install_pulls_no_heavy_or_p2_runtime() -> None:
    closure = base_closure("de-arsenal")

    assert {"arsenal-core", "pugio", "de-gladius", "de-scorpio"} <= closure
    assert closure & HEAVY == set()


def test_p2_tools_keep_their_cluster_runtimes_optional() -> None:
    assert base_closure("de-ballista") & {"pyspark", "kubernetes", "pyiceberg", "obstore"} == set()
    assert base_closure("de-hasta") & {"psycopg", "psycopg-binary", "kafka-python"} == set()


def test_de_arsenal_puts_every_tool_cli_on_path() -> None:
    """`uv tool install de-arsenal` exposes only de-arsenal's own commands, so it must
    declare pugio and gladius as well for the docs' `pugio status` to work."""
    scripts = {
        ep.name: ep.value
        for ep in metadata.distribution("de-arsenal").entry_points
        if ep.group == "console_scripts"
    }
    assert scripts == {
        "arsenal": "arsenal.cli:app",
        "pugio": "pugio.cli:app",
        "gladius": "gladius.cli:app",
    }
    for name in ("pugio", "gladius"):  # the same CLI its own package ships
        [own] = [
            ep
            for ep in metadata.distribution(
                "de-gladius" if name == "gladius" else name
            ).entry_points
            if ep.group == "console_scripts" and ep.name == name
        ]
        assert own.value == scripts[name]
