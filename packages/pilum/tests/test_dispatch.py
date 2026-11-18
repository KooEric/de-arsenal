"""Pilum end to end against an in-process HTTP endpoint (httpx.MockTransport)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from arsenal_core.errors import FatalError
from pilum.ledger import Ledger, RunLock, ledger_path
from pilum.runner import RunReport, dispatch_status, plan_dispatch, run_dispatch
from pilum.spec import DispatchSpec

Handler = Callable[[httpx.Request], httpx.Response]


class NoSleep:
    def __init__(self) -> None:
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return 0.0

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


class Endpoint:
    """Records every request; answers with `respond` (default 200)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.respond: Handler = lambda request: httpx.Response(200)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def bodies(self) -> list[Any]:
        return [json.loads(request.content) for request in self.requests if request.content]


def write_rows(root: Path, rows: list[dict[str, Any]]) -> None:
    target = root / "data" / "risk"
    target.mkdir(parents=True, exist_ok=True)
    for old in target.glob("*.parquet"):
        old.unlink()
    pq.write_table(pa.Table.from_pylist(rows), target / "part-0.parquet")  # pyright: ignore[reportUnknownMemberType]


def make_spec(root: Path, **overrides: Any) -> DispatchSpec:
    destination: dict[str, Any] = {
        "type": "http",
        "url": "https://api.test/alerts",
        "headers": {"Authorization": "Bearer ${PILUM_TEST_TOKEN}"},
        "idempotency_header": "Idempotency-Key",
    }
    destination.update(overrides.pop("destination", {}))
    raw: dict[str, Any] = {
        "name": "churn",
        "source": {"type": "parquet", "path": str(root / "data" / "risk")},
        "key": ["customer_id"],
        "destination": destination,
        "retry": {"max_attempts": 2, "base_wait_seconds": 0},
        "state_dir": str(root / ".arsenal"),
    }
    raw.update(overrides)
    return DispatchSpec.model_validate(raw)


def run(spec: DispatchSpec, endpoint: Endpoint, **flags: bool) -> RunReport:
    return run_dispatch(spec, client=endpoint.client(), clock=NoSleep(), **flags)


ROWS = [
    {"customer_id": 1, "risk_score": 0.9},
    {"customer_id": 2, "risk_score": 0.4},
    {"customer_id": 3, "risk_score": 0.1},
]


@pytest.fixture(autouse=True)
def token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PILUM_TEST_TOKEN", "s3cr3t-token-value")


def test_first_run_sends_everything_and_rerun_sends_nothing(tmp_path: Path) -> None:
    write_rows(tmp_path, ROWS)
    spec, endpoint = make_spec(tmp_path), Endpoint()

    first = run(spec, endpoint)
    second = run(spec, endpoint)

    assert (first.inserted, first.ok) == (3, True)
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, 3)
    assert len(endpoint.requests) == 3
    assert endpoint.bodies()[0] == {"customer_id": 1, "risk_score": 0.9}
    assert endpoint.requests[0].headers["Authorization"] == "Bearer s3cr3t-token-value"


def test_upsert_sends_only_changed_and_new_records(tmp_path: Path) -> None:
    write_rows(tmp_path, ROWS)
    spec, endpoint = make_spec(tmp_path), Endpoint()
    run(spec, endpoint)
    endpoint.requests.clear()

    write_rows(
        tmp_path, [{**ROWS[0], "risk_score": 0.95}, ROWS[1], {"customer_id": 4, "risk_score": 0.7}]
    )
    report = run(spec, endpoint)

    assert (report.inserted, report.updated, report.deleted, report.unchanged) == (1, 1, 0, 1)
    assert sorted(body["customer_id"] for body in endpoint.bodies()) == [1, 4]


def test_insert_mode_never_resends_a_known_key(tmp_path: Path) -> None:
    write_rows(tmp_path, ROWS)
    spec, endpoint = make_spec(tmp_path, mode="insert"), Endpoint()
    run(spec, endpoint)
    endpoint.requests.clear()

    write_rows(tmp_path, [{**ROWS[0], "risk_score": 0.0}, *ROWS[1:]])
    report = run(spec, endpoint)

    assert (report.updated, len(endpoint.requests)) == (0, 0)


def test_mirror_deletes_keys_that_left_the_source(tmp_path: Path) -> None:
    write_rows(tmp_path, ROWS)
    delete = {"url": "https://api.test/alerts/{customer_id}"}
    spec = make_spec(tmp_path, mode="mirror", destination={"delete": delete})
    endpoint = Endpoint()
    run(spec, endpoint)
    endpoint.requests.clear()

    write_rows(tmp_path, ROWS[:2])
    report = run(spec, endpoint)

    assert report.deleted == 1
    assert [(r.method, str(r.url)) for r in endpoint.requests] == [
        ("DELETE", "https://api.test/alerts/3")
    ]
    assert run(spec, endpoint).deleted == 0


def test_mirror_refuses_to_delete_most_of_the_ledger_without_confirmation(tmp_path: Path) -> None:
    write_rows(tmp_path, ROWS)
    delete = {"url": "https://api.test/alerts/{customer_id}"}
    spec = make_spec(tmp_path, mode="mirror", destination={"delete": delete})
    endpoint = Endpoint()
    run(spec, endpoint)
    endpoint.requests.clear()

    write_rows(tmp_path, ROWS[:1])  # two of three keys vanish
    with pytest.raises(FatalError, match="--confirm-deletes"):
        run(spec, endpoint)
    assert endpoint.requests == []

    assert run(spec, endpoint, confirm_deletes=True).deleted == 2


def test_first_dispatch_over_the_limit_is_refused(tmp_path: Path) -> None:
    write_rows(tmp_path, ROWS)
    spec = make_spec(tmp_path, safety={"max_first_dispatch": 2})
    endpoint = Endpoint()

    with pytest.raises(FatalError, match="--confirm-initial-load"):
        run(spec, endpoint)
    assert endpoint.requests == []

    assert run(spec, endpoint, confirm_initial_load=True).inserted == 3


def test_crash_after_response_resends_with_the_same_idempotency_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PL-ADR-2: the endpoint got the record, the ledger write never happened."""

    write_rows(tmp_path, ROWS[:1])
    spec, endpoint = make_spec(tmp_path), Endpoint()

    def crash(*args: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(Ledger, "record_sent", crash)
    with pytest.raises(KeyboardInterrupt):
        run(spec, endpoint)
    monkeypatch.undo()
    monkeypatch.setenv("PILUM_TEST_TOKEN", "s3cr3t-token-value")

    report = run(spec, endpoint)

    assert report.inserted == 1
    keys = [request.headers["Idempotency-Key"] for request in endpoint.requests]
    assert len(keys) == 2 and keys[0] == keys[1]


def test_rotating_the_token_does_not_resend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_rows(tmp_path, ROWS)
    spec, endpoint = make_spec(tmp_path), Endpoint()
    run(spec, endpoint)

    monkeypatch.setenv("PILUM_TEST_TOKEN", "a-brand-new-token")
    report = run(spec, endpoint)

    assert (report.unchanged, len(endpoint.requests)) == (3, 3)


def test_4xx_parks_the_record_and_replay_requeues_it(tmp_path: Path) -> None:
    write_rows(tmp_path, ROWS)
    spec, endpoint = make_spec(tmp_path), Endpoint()
    endpoint.respond = lambda request: httpx.Response(
        422 if json.loads(request.content)["customer_id"] == 2 else 200, text="bad score"
    )

    report = run(spec, endpoint)
    assert (report.inserted, report.parked, report.ok) == (2, 1, False)

    status = dispatch_status(spec)
    assert status["alert"] is True
    assert status["dead_letters"][0]["last_error"] == "HTTP 422: bad score"

    endpoint.requests.clear()
    skipped = run(spec, endpoint)
    assert (skipped.dead, len(endpoint.requests)) == (1, 0)

    ledger = Ledger(ledger_path(spec.state_dir, spec.name))
    assert ledger.requeue() == 1
    ledger.close()
    endpoint.respond = lambda request: httpx.Response(200)
    assert run(spec, endpoint).inserted == 1
    assert dispatch_status(spec)["alert"] is False


def test_a_changed_record_replaces_its_dead_letter(tmp_path: Path) -> None:
    write_rows(tmp_path, ROWS[:1])
    spec, endpoint = make_spec(tmp_path), Endpoint()
    endpoint.respond = lambda request: httpx.Response(400)
    run(spec, endpoint)

    write_rows(tmp_path, [{**ROWS[0], "risk_score": 0.5}])
    endpoint.respond = lambda request: httpx.Response(200)
    report = run(spec, endpoint)

    assert report.inserted == 1
    assert dispatch_status(spec)["dead_letters"] == []


def test_5xx_is_retried_then_left_queued_then_parked(tmp_path: Path) -> None:
    write_rows(tmp_path, ROWS[:1])
    spec = make_spec(tmp_path, dlq={"after_attempts": 4})
    endpoint = Endpoint()
    endpoint.respond = lambda request: httpx.Response(503)

    first = run(spec, endpoint)
    assert (first.failed, first.parked, len(endpoint.requests)) == (1, 0, 2)

    second = run(spec, endpoint)
    assert (second.failed, second.parked, len(endpoint.requests)) == (0, 1, 4)


def test_a_transient_failure_recovers_within_the_run(tmp_path: Path) -> None:
    write_rows(tmp_path, ROWS[:1])
    spec, endpoint = make_spec(tmp_path), Endpoint()
    answers = iter([httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200)])
    endpoint.respond = lambda request: next(answers)

    assert run(spec, endpoint).inserted == 1


def test_a_down_destination_stops_the_run_without_filling_the_dlq(tmp_path: Path) -> None:
    write_rows(tmp_path, [{"customer_id": i, "risk_score": 0.5} for i in range(20)])
    spec = make_spec(tmp_path, safety={"max_consecutive_failures": 3})
    endpoint = Endpoint()

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    endpoint.respond = refuse
    report = run(spec, endpoint)

    assert report.aborted is not None and "unavailable" in report.aborted
    assert len(endpoint.requests) == 3 * 2  # three records, two tries each
    assert dispatch_status(spec)["dead_letters"] == []


def test_refused_credentials_stop_the_run_and_never_leak_the_token(tmp_path: Path) -> None:
    write_rows(tmp_path, ROWS)
    spec, endpoint = make_spec(tmp_path), Endpoint()
    endpoint.respond = lambda request: httpx.Response(
        401, text=f"bad token {request.headers['Authorization']}"
    )

    with pytest.raises(FatalError) as caught:
        run(spec, endpoint)

    assert "s3cr3t-token-value" not in str(caught.value)
    assert len(endpoint.requests) == 1
    status = json.dumps(dispatch_status(spec))
    assert "s3cr3t-token-value" not in status and '"ledger_size": 0' in status


def test_rejection_text_is_masked_in_the_dead_letter(tmp_path: Path) -> None:
    write_rows(tmp_path, ROWS[:1])
    spec, endpoint = make_spec(tmp_path), Endpoint()
    endpoint.respond = lambda request: httpx.Response(
        400, text=f"echo {request.headers['Authorization']}"
    )
    run(spec, endpoint)

    assert dispatch_status(spec)["dead_letters"][0]["last_error"] == "HTTP 400: echo Bearer ***"


def test_body_template_keeps_types_and_never_expands_env_or_record_placeholders(
    tmp_path: Path,
) -> None:
    write_rows(tmp_path, [{"customer_id": 7, "risk_score": 0.25, "note": "${PILUM_TEST_TOKEN}"}])
    body = {
        "id": "{customer_id}",
        "label": "customer {customer_id}",
        "note": "{note}",
        "nested": [{"score": "{risk_score}"}],
        "literal": "${PILUM_TEST_TOKEN}",
        "fixed": 1,
    }
    spec, endpoint = make_spec(tmp_path, destination={"body": body}), Endpoint()
    run(spec, endpoint)

    assert endpoint.bodies() == [
        {
            "id": 7,
            "label": "customer 7",
            "note": "${PILUM_TEST_TOKEN}",
            "nested": [{"score": 0.25}],
            "literal": "${PILUM_TEST_TOKEN}",
            "fixed": 1,
        }
    ]


def test_url_values_are_percent_encoded(tmp_path: Path) -> None:
    write_rows(tmp_path, [{"customer_id": "a/b?c", "risk_score": 1.0}])
    spec = make_spec(
        tmp_path, destination={"url": "https://api.test/c/{customer_id}", "method": "PUT"}
    )
    endpoint = Endpoint()
    run(spec, endpoint)

    assert endpoint.requests[0].url.raw_path == b"/c/a%2Fb%3Fc"


def test_slack_posts_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_HOOK", "https://hooks.test/T/B/x")
    write_rows(tmp_path, ROWS[:1])
    destination = {
        "type": "slack",
        "url": "${SLACK_HOOK}",
        "text": "risk {risk_score} for {customer_id}",
    }
    raw = make_spec(tmp_path).model_dump()
    raw["destination"] = destination
    spec, endpoint = DispatchSpec.model_validate(raw), Endpoint()
    run(spec, endpoint)

    assert str(endpoint.requests[0].url) == "https://hooks.test/T/B/x"
    assert endpoint.bodies() == [{"text": "risk 0.9 for 1"}]
    assert dispatch_status(spec)["destination"]["url"] == "${SLACK_HOOK}"


def test_query_source_and_plan_write_nothing(tmp_path: Path) -> None:
    write_rows(tmp_path, ROWS)
    glob = (tmp_path / "data" / "risk" / "*.parquet").as_posix()
    sql = f"SELECT customer_id, risk_score FROM read_parquet('{glob}') WHERE risk_score > 0.3"
    spec = make_spec(tmp_path, source={"type": "query", "sql": sql})

    plan = plan_dispatch(spec)

    assert plan.summary()["insert"] == 2 and plan.first_dispatch
    assert not (tmp_path / ".arsenal").exists()


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([ROWS[0], ROWS[0]], "more than once"),
        ([{"customer_id": None, "risk_score": 1.0}, ROWS[0]], "null in key"),
        ([{"id": 1}], "does not have"),
    ],
)
def test_bad_sources_are_refused_before_anything_is_sent(
    tmp_path: Path, rows: list[dict[str, Any]], message: str
) -> None:
    write_rows(tmp_path, rows)
    endpoint = Endpoint()

    with pytest.raises(FatalError, match=message):
        run(make_spec(tmp_path), endpoint)
    assert endpoint.requests == []


def test_missing_source_says_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(FatalError, match="run the pipeline that produces it first"):
        plan_dispatch(make_spec(tmp_path))


def test_missing_env_var_is_a_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_rows(tmp_path, ROWS)
    monkeypatch.delenv("PILUM_TEST_TOKEN")

    with pytest.raises(FatalError, match="environment variable not set: PILUM_TEST_TOKEN"):
        run(make_spec(tmp_path), Endpoint())


def test_second_sender_is_refused(tmp_path: Path) -> None:
    write_rows(tmp_path, ROWS)
    spec = make_spec(tmp_path)

    with (
        RunLock(ledger_path(spec.state_dir, spec.name)),
        pytest.raises(FatalError, match="another pilum"),
    ):
        run(spec, Endpoint())

    assert run(spec, Endpoint()).inserted == 3  # the lock is released afterwards


def test_a_newer_ledger_is_refused(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "x.pilum.db"
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version = 99")
    con.close()

    with pytest.raises(FatalError, match="newer pilum"):
        Ledger(path)


def test_arrow_values_become_stable_json() -> None:
    from datetime import UTC, date, datetime, timedelta
    from decimal import Decimal

    from pilum.diff import jsonable

    assert jsonable(
        {
            "at": datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
            "naive": datetime(2026, 1, 2),
            "day": date(2026, 1, 2),
            "price": Decimal("10.50"),
            "raw": b"\x00\x01",
            "took": timedelta(seconds=90),
            "bad": float("nan"),
            "tags": ["a", None],
        }
    ) == {
        "at": "2026-01-02T03:04:00+00:00",
        "naive": "2026-01-02T00:00:00",
        "day": "2026-01-02",
        "price": "10.50",
        "raw": "AAE=",
        "took": 90.0,
        "bad": "nan",
        "tags": ["a", None],
    }
    with pytest.raises(FatalError, match="cannot dispatch"):
        jsonable(object())
