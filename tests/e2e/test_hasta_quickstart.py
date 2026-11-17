"""E2E: examples/hasta streams from the bundled fake API into Parquet (offline).

Runs the real ``fake_orders_api`` on an ephemeral local port and the real
``StreamRunner`` with the example spec, so the README quickstart cannot drift.
"""

from __future__ import annotations

import importlib.util
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml
from hasta.loader import load_stream
from hasta.runner import StreamRunner

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "hasta"


def _load_api() -> Any:
    spec = importlib.util.spec_from_file_location("fake_orders_api", EXAMPLE / "fake_orders_api.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api_url() -> Iterator[str]:
    api = _load_api()
    api._add_orders(120)  # pyright: ignore[reportPrivateUsage]
    server = ThreadingHTTPServer(("127.0.0.1", 0), api.OrdersHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/orders"
    finally:
        server.shutdown()
        server.server_close()


def test_example_stream_ingests_orders_exactly_once(
    api_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    raw = yaml.safe_load((EXAMPLE / "orders-stream.yaml").read_text(encoding="utf-8"))
    raw["source"]["url"] = api_url
    spec_path = tmp_path / "orders-stream.yaml"
    spec_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    runner = StreamRunner(load_stream(spec_path))
    try:
        first = runner.run(max_batches=2)
        second = runner.run(max_batches=5)
    finally:
        runner.close()

    table = cast(pa.Table, pq.read_table(tmp_path / "data" / "orders"))  # pyright: ignore[reportUnknownMemberType]
    ids = cast(list[int], table.column("id").to_pylist())
    assert first.committed == 2
    assert second.committed >= 1  # the caught-up empty poll commits once, then repeats skip
    assert sorted(ids) == list(range(1, 121))
