"""A tiny alerts endpoint for trying Pilum locally (stdlib only).

    python examples/pilum/fake_alerts_api.py            # serves http://127.0.0.1:8766

PUT    /alerts/<id>   stores the JSON body. A repeated ``Idempotency-Key`` is
                      acknowledged without being applied again.
DELETE /alerts/<id>   removes it (404 if it is not there).
GET    /alerts        returns what the endpoint currently holds, plus counters.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_lock = threading.Lock()
alerts: dict[str, Any] = {}
seen_keys: set[str] = set()
stats = {"applied": 0, "duplicates": 0, "deleted": 0}


class AlertsHandler(BaseHTTPRequestHandler):
    def _reply(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _alert_id(self) -> str | None:
        prefix = "/alerts/"
        return self.path[len(prefix) :] if self.path.startswith(prefix) else None

    def do_GET(self) -> None:
        with _lock:
            self._reply(200, {"alerts": alerts, **stats})

    def do_PUT(self) -> None:
        alert_id = self._alert_id()
        if alert_id is None:
            self._reply(404, {"error": "not found"})
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        key = self.headers.get("Idempotency-Key")
        with _lock:
            if key is not None and key in seen_keys:
                stats["duplicates"] += 1
            else:
                if key is not None:
                    seen_keys.add(key)
                alerts[alert_id] = body
                stats["applied"] += 1
        self._reply(200, {"ok": True})

    def do_DELETE(self) -> None:
        alert_id = self._alert_id()
        with _lock:
            found = alert_id is not None and alerts.pop(alert_id, None) is not None
            if found:
                stats["deleted"] += 1
        self._reply(200 if found else 404, {"ok": found})

    def log_message(self, format: str, *args: object) -> None:
        del format, args  # keep the console for pilum's own output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), AlertsHandler)
    print(f"alerts endpoint on http://127.0.0.1:{args.port}/alerts (Ctrl-C to stop)")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    server.server_close()


if __name__ == "__main__":
    main()
