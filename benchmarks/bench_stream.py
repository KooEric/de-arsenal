"""Hasta microbatch throughput — regression reference, not a gate (P2 Phase 3).

Streams N synthetic events from an in-process cursor API (no network) through
the real ``RestPollingSource`` → ``StreamRunner`` → Parquet sink and SQLite
ledger, three times, and reports the median events/s. A second pass over the
already-committed stream measures the cost of a no-op resume.

    uv run python benchmarks/bench_stream.py [--events 1000000] [--page 5000]

Numbers depend on the machine; record them in benchmarks/RESULTS.md with the
date and hardware. Not collected by pytest (lives outside tests/, no test_*).
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import tempfile
import time
from pathlib import Path

import httpx
from hasta.runner import StreamRunner
from hasta.sources.rest_poll import RestPollingSource
from hasta.spec import RestPollSourceSpec, StreamSpec


def api(total: int) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("after", "0"))
        stop = min(start + int(request.url.params["limit"]), total)
        rows = [{"id": i, "amount": i * 0.5, "kind": i % 10} for i in range(start, stop)]
        return httpx.Response(200, json={"data": rows, "next": str(stop) if stop < total else None})

    return httpx.MockTransport(handler)


def spec(root: Path, page: int) -> StreamSpec:
    return StreamSpec.model_validate(
        {
            "name": "bench",
            "state_dir": str(root / "state"),
            "source": {
                "type": "rest_poll",
                "url": "https://bench.invalid/events",
                "limit_param": "limit",
                "record_path": "data",
                "cursor": {
                    "request_param": "after",
                    "response_path": "next",
                    "event_id_path": "id",
                },
            },
            "stream": {
                "name": "bench",
                "poll_interval_seconds": 0.01,
                "batch": {"max_records": page, "max_bytes": 512_000_000, "max_wait_seconds": 1},
                "checkpoint": {"lease_ttl_seconds": 60},
            },
            "sink": {"type": "parquet", "path": str(root / "out")},
        }
    )


def run_once(total: int, page: int) -> tuple[float, float]:
    root = Path(tempfile.mkdtemp(prefix="hasta-bench-"))
    try:
        stream = spec(root, page)
        assert isinstance(stream.source, RestPollSourceSpec)
        client = httpx.Client(transport=api(total))
        timings: list[float] = []
        for _ in range(2):  # full stream, then a no-op resume over committed state
            runner = StreamRunner(stream, source=RestPollingSource(stream.source, client=client))
            started = time.perf_counter()
            report = runner.run()
            timings.append(time.perf_counter() - started)
            runner.close()
            assert report.exhausted
        return timings[0], timings[1]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=1_000_000)
    parser.add_argument("--page", type=int, default=5_000)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    results = [run_once(args.events, args.page) for _ in range(args.repeats)]
    full = statistics.median(r[0] for r in results)
    resume = statistics.median(r[1] for r in results)
    print(f"events={args.events:,} page={args.page:,} batches={args.events // args.page:,}")
    print(f"full stream: median {full:.2f}s  ({args.events / full:,.0f} events/s)")
    print(f"no-op resume: median {resume * 1000:.1f} ms")


if __name__ == "__main__":
    main()
