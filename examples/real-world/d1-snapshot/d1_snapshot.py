#!/usr/bin/env python3
"""Write one Cloudflare D1 table as a snapshot file for `arsenal dataset`.

Read-only: it sends SELECT statements to the D1 HTTP API, paged on the key.
Standard library only.

    export CLOUDFLARE_ACCOUNT_ID=...      # the account that owns the database
    export CLOUDFLARE_API_TOKEN=...       # a token with D1 read permission
    python d1_snapshot.py <database-id> <table> <key-column> input/<table>.jsonl

The output is rewritten whole on every run, rows ordered by key and fields in a
fixed order, so an unchanged table produces a byte-identical file and
`arsenal dataset preview` reports it as unchanged. A `.csv` output path writes
CSV instead (see README.md for when to prefer it).
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API = "https://api.cloudflare.com/client/v4"
PAGE = 1000
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def query(account: str, database: str, token: str, sql: str, params: list[Any]) -> list[dict]:
    request = urllib.request.Request(
        f"{API}/accounts/{account}/d1/database/{database}/query",
        data=json.dumps({"sql": sql, "params": params}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"error: D1 API returned {exc.code}: {exc.read()[:300]!r}") from exc
    if not body.get("success"):
        raise SystemExit(f"error: D1 query failed: {body.get('errors')}")
    return body["result"][0]["results"]


def rows(account: str, database: str, token: str, table: str, key: str):
    sql = (
        f'SELECT * FROM "{table}" WHERE (?1 IS NULL OR "{key}" > ?1) ORDER BY "{key}" LIMIT {PAGE}'
    )
    last = None
    while True:
        page = query(account, database, token, sql, [last])
        if not page:
            return
        yield from page
        last = page[-1][key]


def main() -> int:
    if len(sys.argv) != 5:
        print(__doc__, file=sys.stderr)
        return 2
    database, table, key, out = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
    if not IDENT.match(table) or not IDENT.match(key):
        print("error: table and key must be plain identifiers", file=sys.stderr)
        return 2
    try:
        account = os.environ["CLOUDFLARE_ACCOUNT_ID"]
        token = os.environ["CLOUDFLARE_API_TOKEN"]
    except KeyError as exc:
        print(f"error: environment variable not set: {exc.args[0]}", file=sys.stderr)
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".partial")
    count = 0
    with partial.open("w", encoding="utf-8", newline="") as stream:
        writer = None
        for row in rows(account, database, token, table, key):
            if out.suffix.lower() == ".csv":
                if writer is None:
                    writer = csv.DictWriter(stream, fieldnames=sorted(row))
                    writer.writeheader()
                writer.writerow({k: "" if v is None else v for k, v in row.items()})
            else:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    if count == 0:
        partial.unlink()
        print(f"error: {table} returned no rows; the previous snapshot was kept", file=sys.stderr)
        return 1
    os.replace(partial, out)
    print(f"{table}: {count} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
