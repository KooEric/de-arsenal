"""A tiny cursor-paginated orders API for trying Hasta locally (stdlib only).

    python examples/hasta/fake_orders_api.py            # serves http://127.0.0.1:8765

GET /orders?after=<cursor>&limit=<n> returns up to ``limit`` orders after the
cursor plus ``next_cursor``. New orders appear every few seconds, so a running
``hasta run`` keeps picking them up. The cursor is always returned, even on an
empty page: Hasta v1 treats a null cursor as the permanent end of a stream.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

_lock = threading.Lock()
_orders: list[dict[str, object]] = []


def _add_orders(count: int) -> None:
    with _lock:
        start = len(_orders)
        for offset in range(count):
            order_id = start + offset + 1
            _orders.append(
                {
                    "id": order_id,
                    "customer": f"c-{order_id % 7}",
                    "amount": round(order_id * 1.25, 2),
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )


def _producer(interval: float, batch: int) -> None:
    while True:
        time.sleep(interval)
        _add_orders(batch)


class OrdersHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        url = urlparse(self.path)
        if url.path != "/orders":
            self.send_error(404)
            return
        query = parse_qs(url.query)
        after = int(query.get("after", ["0"])[0])
        limit = max(1, min(int(query.get("limit", ["50"])[0]), 500))
        with _lock:
            page = _orders[after : after + limit]
            end = after + len(page)
        body = json.dumps({"data": page, "next_cursor": str(end)})
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return  # keep the terminal for Hasta's output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--initial", type=int, default=120, help="orders present at start")
    parser.add_argument("--every", type=float, default=3.0, help="seconds between new orders")
    parser.add_argument("--batch", type=int, default=5, help="orders added each time")
    args = parser.parse_args()
    _add_orders(args.initial)
    threading.Thread(target=_producer, args=(args.every, args.batch), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), OrdersHandler)
    print(f"fake orders API on http://127.0.0.1:{args.port}/orders (Ctrl-C to stop)")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
