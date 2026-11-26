# Censor (`de-censor`)

> 감찰관. 파이프라인과 데이터셋을 조사해 등급을 매기고, 고칠 곳을 근거와 함께 남긴다.

```bash
censor audit .                    # grade the project in the current directory (quick tier)
censor audit . --tier standard    # also read run state and the data the project wrote
censor report                     # grade history
censor compare <run_a> <run_b>    # checks whose status changed
arsenal eval .                    # same as `censor audit .`
```

Results go to stdout as JSON (`schemas/censor-report.json`), a summary to stderr, and history
and a Markdown report to `.arsenal/censor/`. Exit code 0 means no failed check, 1 means at
least one, 2 means Censor could not run or could not read the target.

Censor only reads. It never runs a pipeline and never writes to what it audits: the one place
it writes is `.arsenal/censor/` (run state is read from a copy made there, DuckDB files are
attached read-only, Postgres is attached `READ_ONLY`).

## Tiers

| Tier | Reads | Dimensions |
|---|---|---|
| `quick` | project files and their times | correctness, quality guards, security, operability, agent readiness |
| `standard` | also run state and data | the five above, plus reliability, freshness, data health |

- **Operability** includes a file-time check on every tier: when a pipeline's newest output (or,
  for a remote sink, its state file) is older than `--max-age-hours`, or a week when none is
  given, it warns. No data is opened, so a stopped pipeline no longer grades like a running one.
- **Reliability**: failed or quarantined units, the share of rows validation set aside (DLQ),
  whether output is still there after a failure. A pipeline that never ran is not scored.
- **Freshness**: the last completed unit, the `arsenal dataset` publication state, and whether
  any limit says when data is too old (`--max-age-hours`, or `arsenal dataset --max-age-hours`).
  With no limit, a last success older than a week warns instead of passing.
- **Data health**: row count, key uniqueness and NULLs, NULL ratio per column (warn from 50%,
  fail when a column is entirely NULL), and the period a time column covers. For a project the
  data is found from the specs: Parquet and DuckDB sinks, and transform outputs.

## Auditing a dataset without a project

```bash
censor audit --files 'data/*.parquet' --key id --time-column created_at
censor audit --duckdb .arsenal/dataset.duckdb --table published --key id
censor audit --table public.orders --postgres-dsn-env PG_DSN --key id --max-age-hours 24
```

A dataset gets data health and freshness only; history goes to `.arsenal/censor/` in the
current directory (or in `DIR` when one is given). The Postgres DSN is read from the named
environment variable and is never printed. `--files` takes Parquet, CSV/TSV or JSON lines.

## Large data

Before reading, Censor measures: file count and size, DuckDB's row estimate, or Postgres
catalog statistics. Over 2,000 files, 5 GB or 50 million estimated rows (and for views, whose
size is unknown) the data-health checks are skipped and the report says how to narrow the read:

| Option | Effect |
|---|---|
| `--sample 1%` | profile a sample. A duplicate found in it fails; none found only warns |
| `--since 2026-11-01` | only rows from that date (needs `--time-column`) |
| `--full` | read everything |
| `--memory-limit 4GB` | DuckDB memory limit; more spills to `.arsenal/censor/tmp/` |
| `--timeout 120` | seconds per query; a query that runs out is `na`, not a failure |

Samples are repeatable. For files and DuckDB: by key hash when `--key` is given (so every row
of a sampled key is read), otherwise a seeded row sample. For a Postgres table:
`TABLESAMPLE SYSTEM` with a fixed seed, run on the server, whatever the key (a Postgres view
is sampled in DuckDB instead).

Auditing Postgres needs DuckDB's `postgres` extension. DuckDB downloads it once into its own
directory (`~/.duckdb/extensions`); that is the only thing written outside `.arsenal/censor/`.

Design: `docs/plans/2026-11-25-censor-design.md`.
