"""Continuous polling past a null cursor, and token rotation not changing identities."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import httpx
import pyarrow.parquet as pq
from hasta.runner import StreamRunner
from hasta.sources.rest_poll import RestPollingSource
from hasta.spec import RestPollSourceSpec, StreamSpec

from arsenal_core.execution import spec_revision
from arsenal_core.state import StateStore


def stream(root: Path, *, on_null: str = "last_event_id", token: str = "t1") -> StreamSpec:
    return StreamSpec.model_validate(
        {
            "name": "orders",
            "state_dir": str(root / "state"),
            "source": {
                "type": "rest_poll",
                "url": "https://api.test/orders",
                "headers": {"Authorization": f"Bearer {token}"},
                "record_path": "data",
                "cursor": {
                    "request_param": "after",
                    "response_path": "next",
                    "event_id_path": "id",
                    "on_null": on_null,
                },
            },
            "stream": {
                "name": "orders",
                "poll_interval_seconds": 0.01,
                "batch": {"max_records": 10, "max_bytes": 1_000_000, "max_wait_seconds": 1},
                "checkpoint": {"lease_ttl_seconds": 30},
            },
            "sink": {"type": "parquet", "path": str(root / "out")},
        }
    )


class CaughtUpApi:
    """Always answers next=null; `after` filters by id, like a real caught-up feed."""

    def __init__(self) -> None:
        self.events = [1, 2, 3]
        self.afters: list[str | None] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            after = request.url.params.get("after")
            self.afters.append(after)
            floor = int(after) if after else 0
            rows = [{"id": i} for i in self.events if i > floor]
            return httpx.Response(200, json={"data": rows, "next": None})

        return httpx.MockTransport(handler)


def runner(spec: StreamSpec, api: CaughtUpApi) -> StreamRunner:
    assert isinstance(spec.source, RestPollSourceSpec)
    client = httpx.Client(transport=api.transport())
    return StreamRunner(spec, source=RestPollingSource(spec.source, client=client))


def ids(out: Path) -> list[int]:
    found: list[int] = []
    for path in sorted(out.glob("*.parquet")):
        table = pq.read_table(path)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        rows = cast(list[dict[str, Any]], table.to_pylist())  # pyright: ignore[reportUnknownMemberType]
        found.extend(int(row["id"]) for row in rows)
    return sorted(found)


def test_last_event_id_mode_keeps_streaming_after_a_null_cursor(tmp_path: Path) -> None:
    api = CaughtUpApi()
    spec = stream(tmp_path)

    first = runner(spec, api)
    report = first.run(max_batches=3, wait_between_polls=False)
    first.close()
    assert not report.exhausted
    api.events += [4, 5]  # new events arrive later
    second = runner(spec, api)
    second.run(max_batches=3, wait_between_polls=False)
    second.close()

    assert ids(tmp_path / "out") == [1, 2, 3, 4, 5]  # each exactly once
    assert api.afters == [None, "3", "3", "3", "5", "5"]
    store = StateStore(tmp_path / "state" / "orders.db")
    summary = store.stream_batch_summary("orders", "orders")
    checkpoint = store.checkpoint("orders", "orders")
    store.close()
    assert checkpoint is not None and checkpoint.position["cursor"] == "5"
    assert summary.by_status == {"committed": 4}  # 2 data batches + 1 empty per caught-up position


def test_default_mode_still_ends_on_a_null_cursor(tmp_path: Path) -> None:
    api = CaughtUpApi()
    run = runner(stream(tmp_path, on_null="end"), api)
    report = run.run(max_batches=5, wait_between_polls=False)
    run.close()

    assert report.exhausted and api.afters == [None]


def test_rotating_a_header_token_keeps_the_revision(tmp_path: Path) -> None:
    before = spec_revision(stream(tmp_path, token="tok-OLD-9f").revision_payload())
    after = spec_revision(stream(tmp_path, token="tok-NEW-3c").revision_payload())

    assert before == after
    assert "tok-OLD-9f" not in str(stream(tmp_path, token="tok-OLD-9f").revision_payload())


def test_adding_a_header_changes_the_revision(tmp_path: Path) -> None:
    base = stream(tmp_path)
    raw = base.model_dump(mode="json", by_alias=True)
    raw["source"]["headers"]["X-Tenant"] = "acme"
    extra = StreamSpec.model_validate(raw)

    assert spec_revision(base.revision_payload()) != spec_revision(extra.revision_payload())
