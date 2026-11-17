"""Drive one real Ballista run against the kind fixtures and verify the result.

Run from the repository root after ``cluster.yaml`` is applied, the worker
image is loaded into kind, and ``minio``/``iceberg-rest`` resolve to local
port-forwards (see ``.github/workflows/ballista-e2e.yml``)::

    uv run --with "pyiceberg[pyarrow]==0.9.1" python packages/ballista/e2e/run_e2e.py

Checks, in order: live preflight is ready; ``ballista run`` submits;
``ballista reconcile`` reaches SUCCEEDED from the published proof; the Iceberg
output holds exactly the expected rows in one snapshot tagged with the run id;
a further reconcile is a no-op.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pyarrow as pa

HERE = Path(__file__).resolve().parent
CATALOG_URI = "http://iceberg-rest:8181"
S3_ENDPOINT = "http://minio:9000"
EXPECTED = [(1, 20.0), (3, 10.0)]
TIMEOUT_SECONDS = 900


def catalog() -> Any:
    pyiceberg_catalog: Any = importlib.import_module("pyiceberg.catalog")
    return pyiceberg_catalog.load_catalog(
        "e2e",
        type="rest",
        uri=CATALOG_URI,
        **{
            "s3.endpoint": S3_ENDPOINT,
            "s3.access-key-id": os.environ["AWS_ACCESS_KEY_ID"],
            "s3.secret-access-key": os.environ["AWS_SECRET_ACCESS_KEY"],
            "s3.region": os.environ["AWS_REGION"],
        },
    )


def seed_input() -> int:
    rows = pa.table(
        {
            "id": pa.array([1, 2, 3], pa.int64()),
            "amount": pa.array([10.0, -5.0, 2.5], pa.float64()),
            "qty": pa.array([2, 1, 4], pa.int64()),
        }
    )
    cat = catalog()
    cat.create_namespace_if_not_exists("e2e")
    table = cat.create_table_if_not_exists("e2e.orders", schema=rows.schema)
    table.append(rows)
    snapshot = table.refresh().current_snapshot()
    assert snapshot is not None, "seeding produced no snapshot"
    return int(snapshot.snapshot_id)


def write_profile(path: Path, image: str) -> None:
    template = HERE / "pod-template.yaml"
    conf = {
        "spark.kubernetes.driver.podTemplateFile": str(template),
        "spark.kubernetes.executor.podTemplateFile": str(template),
        "spark.sql.extensions": (
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"
        ),
        "spark.sql.catalog.arsenal": "org.apache.iceberg.spark.SparkCatalog",
        "spark.sql.catalog.arsenal.type": "rest",
        "spark.sql.catalog.arsenal.uri": CATALOG_URI,
        "spark.sql.catalog.arsenal.io-impl": "org.apache.iceberg.aws.s3.S3FileIO",
        "spark.sql.catalog.arsenal.s3.endpoint": S3_ENDPOINT,
        "spark.sql.catalog.arsenal.s3.path-style-access": "true",
    }
    profile = {
        "backend": "spark_kubernetes",
        "cluster": {
            "context_env": "KUBECONFIG",
            "namespace": "ballista-e2e",
            "driver_image": image,
            # kind-loaded images have no registry digest.
            "allow_mutable_image": True,
            "service_account": "ballista",
            "master_env": "K8S_MASTER",
        },
        "storage": {
            "staging_uri": "s3://staging/ballista",
            "iceberg_catalog": {"type": "rest", "uri_env": "ICEBERG_REST_URI"},
        },
        "spark": {
            "executor_instances": 1,
            "executor_cores": 1,
            "executor_memory": "1g",
            "conf": conf,
        },
    }
    path.write_text(json.dumps(profile, indent=2), encoding="utf-8")


def ballista(*args: str, check: bool = True) -> dict[str, Any]:
    completed = subprocess.run(["ballista", *args], capture_output=True, text=True, check=False)
    sys.stderr.write(completed.stderr)
    if check and completed.returncode != 0:
        print(completed.stdout)
        raise SystemExit(f"ballista {args[0]} failed with exit code {completed.returncode}")
    return json.loads(completed.stdout) if completed.stdout.strip() else {}


def _summary_value(snapshot: Any, key: str) -> str | None:
    """pyiceberg's Summary is a Mapping in recent releases; older ones expose a dict."""

    summary: Any = snapshot.summary
    if summary is None:
        return None
    properties: Any = getattr(summary, "additional_properties", summary)
    value: Any = properties.get(key)
    return None if value is None else str(value)


def verify_output(run_id: str) -> int:
    table = catalog().load_table("e2e.daily")
    rows = sorted((int(r["id"]), float(r["gross"])) for r in table.scan().to_arrow().to_pylist())
    assert rows == EXPECTED, f"unexpected output rows: {rows}"
    tagged = [s for s in table.snapshots() if _summary_value(s, "de-arsenal.run-id") == run_id]
    assert len(tagged) == 1, f"expected one snapshot for {run_id}, found {len(tagged)}"
    return len(table.snapshots())


def main() -> int:
    image = os.environ.get("BALLISTA_E2E_IMAGE", "ballista-worker:e2e")
    work = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "ballista-e2e"
    work.mkdir(parents=True, exist_ok=True)
    profile = work / "profile.json"
    state = work / "state"
    write_profile(profile, image)
    transform = str(HERE / "transform.yaml")

    snapshot_id = seed_input()
    print(f"seeded e2e.orders at snapshot {snapshot_id}")

    plan = ballista(
        "plan",
        transform,
        "--profile",
        str(profile),
        "--input-version",
        str(snapshot_id),
        "--probe",
        check=False,
    )
    findings = plan.get("preflight", {}).get("environment_findings", [])
    if not plan.get("preflight", {}).get("ready"):
        raise SystemExit(f"preflight not ready: {json.dumps(plan.get('preflight'), indent=2)}")
    print(f"preflight ready (findings: {findings})")

    run_id = f"e2e-{uuid.uuid4().hex[:12]}"
    record = ballista(
        "run",
        transform,
        "--profile",
        str(profile),
        "--input-version",
        str(snapshot_id),
        "--state-dir",
        str(state),
        "--run-id",
        run_id,
    )
    print(f"submitted {run_id}: {record.get('status')}")

    deadline = time.monotonic() + TIMEOUT_SECONDS
    while True:
        record = ballista("reconcile", run_id, "--profile", str(profile), "--state-dir", str(state))
        status = record.get("status")
        print(f"reconcile {run_id}: {status}")
        if status == "succeeded":
            break
        if status in {"failed", "cancelled", "lost"} or time.monotonic() > deadline:
            raise SystemExit(f"run did not succeed: {json.dumps(record, indent=2)}")
        time.sleep(10)

    snapshots = verify_output(run_id)
    again = ballista("reconcile", run_id, "--profile", str(profile), "--state-dir", str(state))
    assert again.get("status") == "succeeded", again
    assert verify_output(run_id) == snapshots, "idempotent reconcile created a snapshot"
    print("ballista e2e passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
