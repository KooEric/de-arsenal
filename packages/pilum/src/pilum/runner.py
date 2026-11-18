"""Plan and run a dispatch."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import httpx

from arsenal_core.errors import FatalError
from arsenal_core.ratelimit import Clock
from pilum.destination import DeliveryExhausted, Destination, RecordRejected
from pilum.diff import DispatchPlan, build_plan, read_source
from pilum.ledger import Ledger, RunLock, ledger_path
from pilum.spec import DispatchSpec


@dataclass
class RunReport:
    dispatch: str
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    failed: int = 0  # temporary failure; tried again on the next run
    parked: int = 0  # moved to the dead-letter queue during this run
    dead: int = 0  # already in the dead-letter queue, skipped
    aborted: str | None = None

    @property
    def ok(self) -> bool:
        return self.aborted is None and self.failed == 0 and self.parked == 0

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": self.ok}


def plan_dispatch(spec: DispatchSpec) -> DispatchPlan:
    """What a run would send, without sending or recording anything."""

    rows = read_source(spec.source)
    path = ledger_path(spec.state_dir, spec.name)
    if not path.exists():
        return build_plan(spec, rows, {}, set())
    ledger = Ledger(path)
    try:
        return build_plan(spec, rows, ledger.entries(), ledger.dead_ids())
    finally:
        ledger.close()


def check_safety(
    spec: DispatchSpec, plan: DispatchPlan, *, confirm_initial_load: bool, confirm_deletes: bool
) -> None:
    """Refuse the runs that are most likely a mistake (PL-ADR-4, PL-ADR-5)."""

    limit = spec.safety.max_first_dispatch
    if plan.first_dispatch and len(plan.actions) > limit and not confirm_initial_load:
        raise FatalError(
            f"first run of '{spec.name}' would send {len(plan.actions)} records "
            f"(safety.max_first_dispatch = {limit}). Check `pilum plan`, then re-run with "
            "--confirm-initial-load"
        )
    deletes = plan.count("delete")
    if deletes and not confirm_deletes:
        share = deletes / plan.ledger_size
        if share > spec.safety.max_delete_fraction:
            raise FatalError(
                f"'{spec.name}' would delete {deletes} of {plan.ledger_size} sent records "
                f"({share:.0%} > safety.max_delete_fraction = "
                f"{spec.safety.max_delete_fraction:.0%}); the source has "
                f"{plan.source_rows} rows. If that is intended, re-run with --confirm-deletes"
            )


def run_dispatch(
    spec: DispatchSpec,
    *,
    confirm_initial_load: bool = False,
    confirm_deletes: bool = False,
    client: httpx.Client | None = None,
    clock: Clock | None = None,
) -> RunReport:
    path = ledger_path(spec.state_dir, spec.name)
    with RunLock(path):
        ledger = Ledger(path)
        destination = Destination(spec.destination, spec.retry, client=client, clock=clock)
        try:
            rows = read_source(spec.source)
            plan = build_plan(spec, rows, ledger.entries(), ledger.dead_ids())
            check_safety(
                spec,
                plan,
                confirm_initial_load=confirm_initial_load,
                confirm_deletes=confirm_deletes,
            )
            report = RunReport(dispatch=spec.name, unchanged=plan.unchanged, dead=plan.dead)
            try:
                _send_all(spec, plan, ledger, destination, report)
            finally:
                ledger.record_run(report.to_dict(), ok=report.ok)
            return report
        finally:
            destination.close()
            ledger.close()


def _send_all(
    spec: DispatchSpec,
    plan: DispatchPlan,
    ledger: Ledger,
    destination: Destination,
    report: RunReport,
) -> None:
    consecutive = 0
    for action in plan.actions:
        try:
            destination.send(action.kind, action.record, action.dispatch_id)
        except RecordRejected as error:
            consecutive = 0
            ledger.park(action.dispatch_id, action.record_key, action.kind, 1, str(error))
            report.parked += 1
            continue
        except DeliveryExhausted as error:
            consecutive += 1
            if consecutive >= spec.safety.max_consecutive_failures:
                # The destination is down. These tries say nothing about the
                # records, so they are not counted toward the dead-letter queue.
                report.failed += 1
                report.aborted = (
                    f"{consecutive} records in a row failed ({error}); the destination "
                    "looks unavailable. Unsent records stay queued for the next run"
                )
                return
            total = ledger.add_attempts(
                action.dispatch_id, action.record_key, error.attempts, str(error)
            )
            if total >= spec.dlq.after_attempts:
                ledger.park(action.dispatch_id, action.record_key, action.kind, total, str(error))
                report.parked += 1
            else:
                report.failed += 1
            continue
        except FatalError as error:
            report.aborted = destination.redact(str(error))
            raise FatalError(report.aborted) from error
        consecutive = 0
        # PL-ADR-2: recorded only now, after the 2xx. A crash between the response
        # and this line re-sends the record next run under the same dispatch id.
        if action.kind == "delete":
            ledger.record_deleted(action.record_key)
            report.deleted += 1
        else:
            assert action.content_hash is not None
            ledger.record_sent(action.record_key, action.content_hash, action.dispatch_id)
            if action.kind == "insert":
                report.inserted += 1
            else:
                report.updated += 1


def dispatch_status(spec: DispatchSpec) -> dict[str, Any]:
    path = ledger_path(spec.state_dir, spec.name)
    destination = spec.destination
    payload: dict[str, Any] = {
        "dispatch": spec.name,
        "mode": spec.mode,
        "destination": {
            "type": destination.type,
            "url": destination.url,  # placeholders are never resolved here
            "delivery": (
                "idempotency key sent; exactly-once if the endpoint honours it"
                if destination.idempotency_header
                else "at-least-once (no idempotency key)"
            ),
        },
        "ledger_size": 0,
        "dead_letters": [],
        "last_run": None,
        "last_success_at": None,
        "alert": False,
    }
    if not path.exists():
        return payload
    ledger = Ledger(path)
    try:
        letters = ledger.dead_letters()
        payload.update(
            ledger_size=ledger.size(),
            dead_letters=[asdict(letter) for letter in letters],
            last_run=ledger.last_run(),
            last_success_at=ledger.last_success_at(),
        )
        last = payload["last_run"]
        payload["alert"] = bool(letters) or (last is not None and not last["ok"])
    finally:
        ledger.close()
    return payload
