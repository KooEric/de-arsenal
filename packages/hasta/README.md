# Hasta (`de-hasta`)

Leased REST and Postgres outbox microbatch ingestion with durable checkpoints
and deterministic batch identities. CDC v1 consumes immutable `I`/`U`/`D`
outbox envelopes ordered by `sequence`, then applies them transactionally to
DuckDB or Postgres with an event-id ledger for replay safety.

`hasta run stream.yaml --once` processes at most one available batch. A table
cursor declaration is validated conservatively but is not treated as CDC: it
cannot observe hard deletes, so use an outbox when deletes matter. Kafka,
binlog/WAL or other log-based CDC, raw SQL, and long-running daemon supervision
are not part of v1.

## Quickstart

```bash
python examples/hasta/fake_orders_api.py          # terminal 1: a local cursor API
hasta run examples/hasta/orders-stream.yaml       # terminal 2: stream until stopped
```

`Ctrl-C` (or `SIGTERM` from a supervisor) finishes the batch in flight, commits
it, releases the lease and prints the run report with `"stopped": true`. A second
signal aborts immediately; the next run resumes from the last committed batch.

## Operating a stream

| Command | What it does |
|---|---|
| `hasta run spec.yaml --once` / `--max-batches N` | Process one or N batches, then exit |
| `hasta checkpoint show spec.yaml` | JSON: checkpoint (generation, position, watermark), lease holder and expiry, ledger totals by status, rows/bytes/late rows, sink target. `hasta status` is an alias |
| `hasta checkpoint reset spec.yaml` | Dry-run: prints the checkpoint before/after and what will be re-written |
| `hasta checkpoint reset spec.yaml --confirm` | Starts a new generation from the source's beginning. Refused while a live runner holds the lease |

A reset keeps the batch ledger for audit and gives every replayed batch a new
identity, so the replay is written again. A Parquet sink appends files: clear
its output first if you do not want both copies. DuckDB/Postgres sinks upsert
by `merge_key`.

### Guarantees

Each batch is written before its checkpoint advances, and the checkpoint commit
is fenced by the lease. A crash at any point replays at most the uncommitted
batch under the same identity, which the sink writes idempotently: 100,000
events through 12 crashing runners arrive exactly once
(`tests/test_resume_100k.py`).

### Limits (v1)

- By default a null cursor ends the stream permanently (`_hasta_exhausted`).
  For a feed that answers `next: null` when it is caught up, set
  `cursor.on_null: last_event_id`: the stream keeps polling, asking for events
  after the last event id it committed (the API must accept that id as the
  cursor). Empty caught-up polls share one ledger row.
- Put tokens in `headers` through `${ENV_VAR}` (substituted when the spec loads).
  Header *values* are not part of the stream revision, so rotating a token
  never changes batch identities.
- One runner per stream (single-host SQLite lease); no daemon supervision.
