"""Transfer loop: per unit, export (source engine → S3), then load (S3 → Redshift), then done.

Rerunning resumes. Done units are skipped. A unit whose export finished but whose
load failed keeps its export: the next run goes straight to ``COPY`` instead of
running the long source query again. The finished export is recorded in the
state DB's ``cursors`` table under ``export:<unit_id>``, so it survives a crash.

The run stops at the first unit that still fails after in-process retries; the
units after it stay pending for the next run.
"""

from __future__ import annotations

import functools
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from arsenal_core.errors import ArsenalError
from arsenal_core.retry import DEFAULT_BASE_WAIT, with_retry
from arsenal_core.state import StateStore, UnitRecord
from pugio.transfer.columns import Column, compare_columns
from pugio.transfer.exporters import Exporter, ExportResult, HiveExporter, RedshiftUnloadExporter
from pugio.transfer.loader import RedshiftCopyLoader
from pugio.transfer.spec import TransferSpec
from pugio.transfer.units import TransferUnit, plan_units
from pugio.writer_guard import hold_lease

logger = logging.getLogger("pugio.transfer")


class Loader(Protocol):
    def columns(self) -> list[Column]: ...
    def load(self, unit: TransferUnit, *, empty: bool) -> int: ...


@dataclass(frozen=True)
class TransferReport:
    loaded: int  # units loaded in this run
    skipped: int  # units already done before this run
    exported: int  # source queries run in this run
    reused_exports: int  # loads that reused an export from an earlier run
    rows: int  # rows copied into the target in this run


@dataclass(frozen=True)
class FailedUnit:
    unit_key: str
    error: str


@dataclass(frozen=True)
class TransferStatus:
    counts: dict[str, int]
    failed: list[FailedUnit]


def state_file(spec: TransferSpec) -> Path:
    return Path(spec.state_dir) / f"{spec.name}.transfer.db"


def _export_key(unit: TransferUnit) -> str:
    return f"export:{unit.unit.unit_id}"


def _saved_export(store: StateStore, spec: TransferSpec, unit: TransferUnit) -> ExportResult | None:
    raw = store.get_cursor(spec.name, _export_key(unit))
    if raw is None:
        return None
    saved = json.loads(raw)
    if saved.get("uri") != unit.uri:
        return None  # staging_path changed: the files are not where this run looks
    return ExportResult(rows=saved.get("rows"), empty=bool(saved["empty"]))


def default_exporter(spec: TransferSpec) -> Exporter:
    if spec.source.engine == "redshift":
        return RedshiftUnloadExporter(spec.source)
    return HiveExporter(spec.source)


def run_transfer(
    spec: TransferSpec,
    *,
    exporter: Exporter | None = None,
    loader: Loader | None = None,
    retry_wait: float = DEFAULT_BASE_WAIT,
    clock: Callable[[], float] = time.monotonic,
    reexport: bool = False,
) -> TransferReport:
    """``reexport`` ignores exports saved by earlier runs, e.g. after their files expired."""
    exporter = exporter or default_exporter(spec)
    loader = loader or RedshiftCopyLoader(spec.target)
    retry = functools.partial(with_retry, max_attempts=spec.max_attempts, base_wait=retry_wait)
    loaded = skipped = exported = reused = rows = 0

    path = state_file(spec)
    store = StateStore(path)
    try:
        with hold_lease(
            store,
            path,
            f"transfer:{spec.name}",
            busy=f"transfer {spec.name!r} is busy",
            detail=": another run is in progress. A crashed run's lease expires by itself",
            lost=f"transfer {spec.name!r} lost its run lease; stopped. Rerun to resume",
        ) as check:
            units = plan_units(spec)
            for unit in units:
                store.register(unit.unit)
            pending = [unit for unit in units if not store.is_done(unit.unit.unit_id)]
            skipped = len(units) - len(pending)
            if pending and spec.check_columns:
                compare_columns(
                    exporter.columns(),
                    loader.columns(),
                    table=spec.target.table,
                    check_types=spec.check_types,
                )
            for unit in pending:
                check()
                uid = unit.unit.unit_id
                store.mark_running(uid)
                started = clock()
                try:
                    result = None if reexport else _saved_export(store, spec, unit)
                    if result is None:
                        logger.info("exporting %s to %s", unit.unit.unit_key, unit.uri)
                        result = retry(functools.partial(exporter.export, unit))
                        payload = {"uri": unit.uri, "rows": result.rows, "empty": result.empty}
                        store.set_cursor(spec.name, _export_key(unit), json.dumps(payload))
                        exported += 1
                    else:
                        reused += 1
                    logger.info("loading %s into %s", unit.unit.unit_key, spec.target.table)
                    copied = retry(functools.partial(loader.load, unit, empty=result.empty))
                except ArsenalError as error:
                    store.mark_failed(uid, str(error))
                    raise
                store.mark_done(uid, row_count=copied, duration_ms=int((clock() - started) * 1000))
                loaded += 1
                rows += copied
    finally:
        store.close()
    return TransferReport(loaded, skipped, exported, reused, rows)


def transfer_status(spec: TransferSpec) -> TransferStatus:
    path = state_file(spec)
    if not path.exists():
        return TransferStatus(counts={}, failed=[])
    store = StateStore(path)
    try:
        counts = store.counts(spec.name)
        failed = [
            FailedUnit(unit.unit.unit_key, record.last_error or "")
            for unit in plan_units(spec)
            if (record := _record(store, unit.unit.unit_id)) is not None
            and record.status == "failed"
        ]
    finally:
        store.close()
    return TransferStatus(counts=dict(counts), failed=failed)


def _record(store: StateStore, uid: str) -> UnitRecord | None:
    return store.get(uid) if store.status(uid) is not None else None
