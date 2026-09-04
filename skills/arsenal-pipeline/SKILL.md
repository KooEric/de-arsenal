---
name: arsenal-pipeline
description: Build and run resumable, idempotent data ingestion with DE Arsenal (pugio) — REST API, files, or databases into Parquet, DuckDB, or Postgres — using a declarative collect.yaml instead of pipeline code. Use when the user wants to collect, ingest, extract, load, or sync data from an API/CSV/DB, mentions pugio, collect.yaml, arsenal run/init, or asks for a pipeline that survives interruption without duplicates.
---

# arsenal-pipeline

DE Arsenal is a free, local-first data toolkit: no server, no account, no metering.
Ingestion is `pugio` (also exposed as `arsenal collect`). You write one YAML and one
command; the engine owns retries, pagination, auth refresh, resume and idempotency.

## Scope

- Author `collect.yaml` (a `PipelineSpec`): one `source`, one `sink`, optional `validate`,
  `contract`, `schema_drift`. Full field reference: `references/pipeline-schema.md`.
- Run and re-run it. Re-run = resume: finished units are skipped, unfinished ones are
  fetched again, sinks are idempotent, so interruptions never create duplicates or gaps.
- Not this skill: transformations (`arsenal-transform`), validation rule design
  (`arsenal-quality`), failure triage (`arsenal-operate`), scheduling in the cloud
  (`arsenal-deploy`).

## Inspect first

1. **Is arsenal installed?** `arsenal --help` or `pugio --help`. If not:
   `uv tool install de-arsenal` (or `pipx install de-arsenal`). Postgres sink needs the
   extra: `uv tool install "de-arsenal[postgres]"`. Python 3.11+.
2. **Is there a project?** Look for `arsenal.yaml` (lists `pipelines:` and `transforms:`),
   `collect.yaml`, `.arsenal/` (state). `arsenal init --list` shows starter recipes
   (`github-issues`, `csv-cleanup`, `api-to-postgres`).
3. **Look at the data before writing YAML.** Never guess field names or pagination.
   - REST: fetch one page with the user's token from an env var, e.g.
     `curl -s -H "Authorization: Bearer $TOKEN" "<url>?limit=5"`. Find: is the record
     list the whole body or under a key (`record_path`)? How does the next page work:
     `offset`, `page` number, a cursor field in the body (`cursor_path`), or a `Link`
     header (`link`)?
   - File: `head`, encoding (euc-kr is common in Korean exports), format (csv/jsonl/excel).
   - Database: the table needs a monotonic key (PK/serial/timestamp) for `split.key`.
4. **Secrets stay out of YAML.** Every credential is referenced by env-var *name*
   (`token_env`, `dsn_env`, `client_secret_env`, or `${VAR}` inside `headers`). Ask the
   user for the variable name, never the value.

## Writing collect.yaml

Minimal REST → Parquet:

```yaml
name: github-issues                 # unique per pipeline; state lives in .arsenal/<name>.db
source:
  type: rest
  url: https://api.github.com/repos/duckdb/duckdb/issues
  pagination: { mode: link, size_param: per_page, size: 100 }
  auth: { type: static_token, token_env: GITHUB_TOKEN }
  rate_limit: { rps: 5 }
sink:
  type: parquet
  path: ./data/issues
```

Pick the source:

| Source | When | Key fields |
|---|---|---|
| `rest` | HTTP JSON API | `pagination.mode` offset\|page\|cursor\|link, `record_path` for envelopes, `auth` static_token\|oauth2_client_credentials, `rate_limit.rps`, `encoding`, `method: POST` + `body` |
| `file` | CSV/JSONL/Excel on disk | `path` glob (`./raw/**/*.csv`), `format`, `encoding` |
| `database` | postgres/mysql/sqlite table | `dsn_env`, `table`, `split: {key, chunk}` — units are key ranges, and a re-run re-queries `max(key)` so new rows become new units (incremental for free) |
| `python` | API the REST spec cannot express (e.g. next cursor = last element's id, like Stripe) | `target: "pkg.module:Class"` implementing the Source protocol |
| `dlt` | reuse an existing dlt source | `target`, `resources`; needs the `dlt` extra |

Pick the sink:

| Sink | Idempotency | Notes |
|---|---|---|
| `parquet` | one deterministic file per unit, atomically replaced | default; works with `s3://`, `gs://` via DuckDB httpfs |
| `duckdb` | upsert on `merge_key` | local analytics DB file |
| `postgres` | `INSERT … ON CONFLICT (merge_key) DO UPDATE` | needs `[postgres]` extra; `merge_key` must be the table's primary key |

Cursor mode gotcha: `cursor_path` is a dot-path into a dict (`meta.next_cursor`). It cannot
index lists or mean "last element". If the API's next cursor is the last record's id, use a
`python` source (see `examples/real-world/stripe.yaml` in the repo).

### Recurring pulls: `incremental`

For "every day, just the new orders", declare a time window. Each unit is one interval
`[since, until)`, fully paginated; finishing an interval advances a watermark, so the next
run starts there and completed intervals are never fetched again.

```yaml
source:
  type: rest
  url: https://api.shop.com/orders
  pagination: { mode: offset, size: 100 }     # offset or page only
  incremental:
    since_param: updated_after    # request param carrying the window start
    until_param: updated_before   # optional; omit to send only the start
    start: "2026-01-01T00:00:00Z" # first run only; the watermark wins afterwards
    window: 1d                    # interval size: s|m|h|d|w
    lag: 15m                      # leave the most recent data alone this long
    format: iso8601               # iso8601 | date | epoch_s | epoch_ms
```

Choose from the API's own filter: whatever param it accepts for "changed after" is
`since_param`, and its accepted encoding is `format`. Pair it with a keyed sink
(`duckdb`/`postgres` `merge_key`) so a row the source returns twice upserts instead of
duplicating. Then schedule `arsenal run` (see `arsenal-deploy`).

## Run

```bash
pugio run collect.yaml            # or: arsenal run  (runs everything in arsenal.yaml)
pugio status collect.yaml         # done/pending/failed/quarantined counts
pugio status collect.yaml --cost --max-age-seconds 86400   # rows, bytes, freshness alert
```

Output ends with `done: fetched=N written=N skipped=N quarantined=N`. On error it prints
`error: …` and exits 1 — hand that line to `arsenal-operate`.

## Safety

- Show the YAML to the user before the first run, and always before running against a
  Postgres DSN or any sink that is not a local directory.
- Do not delete `.arsenal/` (state) or sink output unless the user asks. Deleting state
  makes the next run fetch everything again; parquet/upsert sinks stay duplicate-free, but
  API quota and time are spent.
- Re-running is safe by design. Prefer re-run over any manual repair.
- Never write a secret value into YAML, a shell history line, or a commit.

## Honest limits (tell the user, do not work around silently)

- **Incremental REST needs `offset` or `page`.** `cursor`/`link` cannot be rewound to a
  time window, so `incremental` with those modes is rejected at load time. Without
  `incremental`, a REST pipeline pulls a dataset once and then re-runs as a no-op.
- **Data lags by up to `window + lag`.** Only completed windows are collected, so a
  partial window waits. Want fresher? Shrink `window` and run that often.
- **No re-collection of a past window.** Deterministic unit ids and re-fetching the same
  range are incompatible. For data that arrives late at the source, raise `lag`.
- **Use `on_violation: block` on incremental pipelines.** A quarantined page does not hold
  the watermark back, so its rows can be dropped silently; `block` stops the run and keeps
  the watermark until you fix the cause. `pugio dlq retry` is refused for incremental
  pipelines for the same reason.
- No streaming, no sub-minute latency. Polling micro-batch by re-running on a schedule is
  the supported shape.
- Single node: comfortable to hundreds of GB on one machine; not TB-scale.

## Verify

- `pugio status collect.yaml` shows no `failed`; `quarantined` is explained (`arsenal-quality`).
- Count and sample the sink: `arsenal query "SELECT count(*) FROM './data/issues/*.parquet'"`.
- Run it twice. The second run must report `skipped` = all units and write nothing new.
