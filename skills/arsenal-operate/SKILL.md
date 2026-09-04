---
name: arsenal-operate
description: Diagnose and recover DE Arsenal pipeline failures — read the error line, classify Fatal vs Retryable vs AuthExpired, resume safely, inspect state and DLQ, backfill or compact Parquet with onager — without deleting state or creating duplicates. Use when an arsenal/pugio/gladius run failed, was interrupted, printed "error:", needs re-running, re-processing, backfilling, or when Parquet directories have too many small files.
---

# arsenal-operate

The operating model: **re-running is the normal recovery**. State lives in SQLite
(`.arsenal/<pipeline>.db`, `.gladius/`), every unit has a deterministic id, and sinks are
idempotent. Most incidents end with the same command that failed.

## Inspect first

1. Capture the last `error: …` line and the exit code (1 = ArsenalError).
2. `pugio status collect.yaml` — counts of done/pending/failed/quarantined.
3. `pugio dlq list collect.yaml` — quarantined units and reasons.
4. Only then decide. Do not touch files under `.arsenal/`, `.gladius/`, or sink dirs yet.

## Classify

| Error family | Meaning | Action |
|---|---|---|
| `RetryableError` (network, 5xx, 429 exhausted, lock contention) | transient | re-run the same command; the engine already retried with backoff |
| `AuthExpiredError` | token expired | the engine refreshes and resumes automatically (`oauth2_client_credentials`); if it persists, the credential itself is wrong — ask the user to check the env var |
| `FatalError` (invalid YAML, unknown field, unsupported cast type, missing extra, bad DSN/auth) | needs a change | fix the spec or environment, then re-run |

Known misclassification: for `database` sources on postgres/mysql (DuckDB scanner path), a
wrong password can surface as Retryable and be retried until backoff is exhausted. If a
database source "keeps retrying", check credentials first.

Common fatal messages and fixes:

- `postgres sink requires the 'postgres' extra` → `uv tool install "de-arsenal[postgres]"`.
- `dbt stage requires the 'dbt' extra` → `[dbt]` extra.
- `unsupported cast type` → use one of bigint integer double varchar boolean date timestamp decimal.
- `refusing to overwrite existing file` from `arsenal init` → scaffold into an empty dir.
- statement separator `;` rejected in filter/derive/map/sql → split the logic into steps.

## Resume and re-run semantics

- Interrupted run (Ctrl-C, crash, network): re-run. Completed units are skipped; the unit
  in flight is fetched again and overwrites its own output.
- Completed run re-run: no-op (`skipped` = all). This is expected, not a bug.
- Changing `name` or `state_dir` starts a new state: everything is fetched again. Sinks stay
  duplicate-free (deterministic filenames / upsert) but quota and time are spent.
- Upgrading across `0.2.x → 0.3.0` changed unit ids for REST `offset`/`page`: clear that
  pipeline's `state_dir` **and** its parquet sink dir before the first 0.3.0 run, or the
  dataset will contain both old and new files. Tell the user before doing this.

## DLQ

```bash
pugio dlq list collect.yaml
pugio dlq retry collect.yaml --unit <unit_id>     # then re-run
```

Refused for REST `cursor`/`link` (forward-only cursor). For those, re-collect under a new
pipeline name once the cause is fixed.

## Backfill and reprocessing

- Transform logic changed → `gladius run` recomputes in full when the spec hash changes.
- Re-collect a range → for `database` sources, a new `name` with the same `split.key` and a
  filtered table/view; for REST, a new `name` (no range filter exists yet).
- Isolated backfill workspace (no CLI yet, Python API in `onager.backfill`):
  `create_backfill_workspace` → `run_backfill` → verify with queries → `promote_backfill`.
  The active dataset is untouched until `promote_backfill`. Always verify before promoting.

## Small files

```bash
onager compact ./data/issues --dry-run          # plan: which files, how many bytes
onager compact ./data/issues --target-bytes 134217728
```

Local Parquet only (no S3/GCS). Takes a file lock, backs up originals, and restores on
failure. Run it while no collection is writing to that directory.

## Safety

- Never delete state or data as a first move. Explain what a deletion re-fetches and get
  explicit agreement.
- Never edit `.arsenal/*.db` directly.
- Never loosen validation to make a failing run pass (see `arsenal-quality`).
- Re-run before repair. If a re-run reproduces the same Fatal error, the fix is in the spec
  or environment, not in state.

## Verify

- After recovery: `pugio status` shows 0 failed, and a further re-run skips everything.
- Sink count matches expectation: `arsenal query "SELECT count(*) FROM './data/x/*.parquet'"`.
- For upsert sinks, no duplicate keys: `SELECT merge_key, count(*) … GROUP BY 1 HAVING count(*) > 1` is empty.
