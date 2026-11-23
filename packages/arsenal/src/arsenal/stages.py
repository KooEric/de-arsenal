"""Optional stages behind extras: Hasta streams and Ballista submissions.

Both tools are optional installs. A manifest that uses one without the extra
gets the exact install command instead of an ImportError, and the base
``de-arsenal`` install never imports them.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from arsenal.project import DistributedRef, StreamRef
from arsenal_core.errors import ArsenalError, FatalError
from arsenal_core.execution import canonical_hash
from arsenal_core.spec import load_pipeline
from arsenal_core.state import StateStore

_EXTRAS = {
    "hasta": ("streaming", "streams"),
    "ballista": ("distributed", "distributed"),
}


def require_tool(module: str) -> None:
    if importlib.util.find_spec(module) is None:
        extra, section = _EXTRAS[module]
        raise FatalError(
            f"arsenal.yaml has `{section}:` but {module} is not installed: "
            f"pip install 'de-arsenal[{extra}]'  (or: uv tool install 'de-arsenal[{extra}]')"
        )


def run_stream(ref: StreamRef) -> dict[str, Any]:
    """One bounded catch-up pass: at most ``max_batches`` batches, then return."""

    require_tool("hasta")
    loader: Any = importlib.import_module("hasta.loader")
    runner_module: Any = importlib.import_module("hasta.runner")
    runner = runner_module.StreamRunner(loader.load_stream(ref.spec))
    try:
        report = runner.run(max_batches=ref.max_batches)
    finally:
        runner.close()
    return dict(report.__dict__)


def _fingerprint(uri: str) -> str:
    """Current listing fingerprint of a Parquet input prefix (seam for tests)."""

    storage: Any = importlib.import_module("ballista.storage")
    return str(storage.fingerprint_uri(uri))


def resolve_input_version(ref: DistributedRef) -> str:
    """The concrete input version a run is keyed by.

    ``auto`` is not a version: it means "whatever the input is now". It is
    resolved to the input's listing fingerprint *before* the run id is derived,
    so new input data gets a new run instead of reconciling the last one.
    """

    if ref.input_version != "auto":
        return ref.input_version
    loader: Any = importlib.import_module("gladius.loader")
    uri = str(loader.load_transform(ref.transform).input)
    try:
        return _fingerprint(uri)
    except ArsenalError as exc:
        raise FatalError(
            f"cannot resolve input_version: auto for {ref.transform.name}: {exc}"
        ) from exc


def distributed_run_id(project: str, ref: DistributedRef, input_version: str | None = None) -> str:
    """Same project + transform + input version → same run, so re-running resumes.

    Call with the project directory as the working directory (``arsenal run``
    and ``arsenal status`` do), so the id survives moving the project. Pass the
    resolved ``input_version`` when the manifest says ``auto``.
    """

    try:
        transform = ref.transform.relative_to(Path.cwd()).as_posix()
    except ValueError:
        transform = ref.transform.as_posix()
    version = input_version if input_version is not None else ref.input_version
    payload = {"project": project, "transform": transform, "input": version}
    return f"arsenal-{canonical_hash(payload)[:16]}"


def submit_or_reconcile(project: str, ref: DistributedRef) -> dict[str, Any]:
    """Submit a new Ballista run, or reconcile the one this input already started."""

    require_tool("ballista")
    version = resolve_input_version(ref)
    run_id = distributed_run_id(project, ref, version)
    status = _ballista("status", run_id, "--state-dir", str(ref.state_dir), check=False)
    if status is None:
        return _ballista(
            "run",
            str(ref.transform),
            "--profile",
            str(ref.profile),
            "--input-version",
            version,
            "--state-dir",
            str(ref.state_dir),
            "--run-id",
            run_id,
        ) or {"run_id": run_id}
    return _ballista(
        "reconcile", run_id, "--profile", str(ref.profile), "--state-dir", str(ref.state_dir)
    ) or {"run_id": run_id}


def _ballista(*args: str, check: bool = True) -> dict[str, Any] | None:
    completed = subprocess.run(
        [sys.executable, "-m", "ballista.cli", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        if not check:
            return None
        detail = completed.stderr.strip().splitlines()[-1:] or ["no detail"]
        raise FatalError(f"ballista {args[0]} failed: {detail[0]}")
    try:
        parsed: Any = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise FatalError(f"ballista {args[0]} printed no JSON record") from exc
    return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else None


def needs_attention(status: dict[str, Any]) -> bool:
    """True when a stream's health or a run history's summary raises an alert."""

    block: Any = status.get("health") or status.get("history")
    if not isinstance(block, dict):
        return False
    return bool(cast(dict[str, Any], block).get("alert"))


# Unit states that mean a batch pipeline lost data until someone acts on it.
_PIPELINE_ALERT_STATES = ("failed", "quarantined")


def pipeline_status(path: Path) -> dict[str, Any]:
    """Unit counts of one Pugio pipeline, like `pugio status` (a never-run one creates no state)."""

    spec = load_pipeline(path)
    db_path = Path(spec.state_dir) / f"{spec.name}.db"
    base: dict[str, Any] = {"pipeline": spec.name, "spec": str(path)}
    if not db_path.exists():
        return {
            **base,
            "status": "never-run",
            "units": {},
            "last_completed_at": None,
            "alert": False,
        }
    store = StateStore(db_path)
    try:
        units = store.counts(spec.name)
        last_completed_at = store.pipeline_metrics(spec.name).last_completed_at
    finally:
        store.close()
    alert = any(units.get(state, 0) for state in _PIPELINE_ALERT_STATES)
    return {
        **base,
        "status": "needs-attention" if alert else ("ok" if units else "never-run"),
        "units": units,
        "last_completed_at": last_completed_at,
        "alert": alert,
    }


def stream_status(ref: StreamRef) -> dict[str, Any]:
    require_tool("hasta")
    loader: Any = importlib.import_module("hasta.loader")
    status_module: Any = importlib.import_module("hasta.status")
    state: Any = importlib.import_module("arsenal_core.state")
    spec = loader.load_stream(ref.spec)
    store = state.StateStore(status_module.state_path(spec))
    try:
        return dict(status_module.stream_status(spec, store))
    finally:
        store.close()


def distributed_status(project: str, ref: DistributedRef) -> dict[str, Any]:
    """Durable view only — never contacts the cluster (use reconcile for that).

    With ``input_version: auto`` it lists the input prefix to find the current
    run; when that listing is impossible it reports ``input-unresolved``.
    """

    from scorpio import RunObservation, summarize_runs

    from arsenal_core.state import StateStore

    try:
        version = resolve_input_version(ref)
    except FatalError as exc:  # e.g. no storage credentials where `status` runs
        return {"run_id": None, "status": "input-unresolved", "error": str(exc), "history": None}
    run_id = distributed_run_id(project, ref, version)
    db = Path(ref.state_dir) / "ballista.sqlite"
    if not db.exists():
        return {"run_id": run_id, "status": "not-submitted", "history": None}
    store = StateStore(db)
    try:
        record = store.run(run_id)
        history = store.runs(pipeline=record.pipeline) if record is not None else []
    finally:
        store.close()
    summary = summarize_runs(
        [RunObservation(r.run_id, r.status, r.attempt, r.created_at, r.updated_at) for r in history]
    )
    return {
        "run_id": run_id,
        "status": record.status if record is not None else "not-submitted",
        "attempt": record.attempt if record is not None else None,
        "history": {
            "total": summary.total,
            "by_status": summary.by_status,
            "retried": summary.retried,
            "last_success_at": summary.last_success_at,
            "mean_duration_seconds": summary.mean_duration_seconds,
            "alert": summary.alert,
            "reasons": list(summary.reasons),
        },
    }


def catalog_status(project_dir: Path) -> dict[str, Any] | None:
    """Aquila summary when ``de-aquila`` is installed and ``.arsenal/aquila.db`` exists.

    Read-only: run ``aquila scan`` to refresh it. Contract findings raise the alert.
    """

    db = project_dir / ".arsenal" / "aquila.db"
    if importlib.util.find_spec("aquila") is None or not db.is_file():
        return None
    store_module: Any = importlib.import_module("aquila.store")
    service: Any = importlib.import_module("aquila.service")
    store = store_module.CatalogStore(db)
    try:
        findings = [
            f"{dataset.name}: {finding}"
            for dataset in store.datasets()
            for finding in service.contract_findings(store, dataset)
        ]
        return {
            "db": str(db),
            "totals": dict(store.counts()),
            "contract_findings": findings,
            "warnings": list(service.deprecation_warnings(store)),
            "alert": bool(findings),
        }
    finally:
        store.close()
