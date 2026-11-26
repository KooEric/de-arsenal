# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project has not yet reached a public API stability commitment (see
[Known limitations](#known-limitations)); spec-field additions are
backward-compatible by policy (every internal dependency is pinned to the
current minor, e.g. `>=0.4,<0.5`). Because `0.x` treats the *minor* as the
breaking axis, a release that changes behaviour incompatibly takes the next
minor.

## [Unreleased]

## [0.7.2] - 2026-11-26

Aquila stops raising false contract alerts on Parquet datasets. All 14 packages move to
`0.7.2`; internal pins stay `>=0.7,<0.8`. One state migration: `aquila.db` v3 → v4.

0.7.1 was tagged but never published to PyPI (its release run was cancelled), so this is the
first release with the 0.7.1 changes below. Upgrading from 0.7.0, read both sections.

### ⚠️ Upgrade notes

- The first Aquila command after upgrading migrates `.arsenal/aquila.db` from v3 to v4 (a new
  `null_counts` table; nothing is rewritten). The migration is one way: Aquila 0.7.0 and
  older refuse a v4 catalog ("newer than this Aquila"). To go back, restore a copy of
  `aquila.db` taken before the upgrade, or delete it and run `aquila scan` again.
- Run `aquila scan` once after upgrading so the null counts are recorded. Until then a NOT
  NULL column has no statistics and raises no finding.

### Fixed

- Aquila no longer reports false contract findings on Parquet datasets, so `arsenal status`
  stops raising `alert: true` for data that meets its contract. A NOT NULL column is now
  judged by the null counts in the newest file's footer statistics, because Parquet marks
  every field nullable. A column with no statistics raises no finding. A `timestamp[s]`
  contract accepts a stored `timestamp[ms]` (same time zone), because Parquet has no seconds
  unit. A different base type, another timestamp unit, or real nulls in a NOT NULL column are
  still reported. Null counts go into a new `null_counts` table (`aquila.db` v4, migrated on
  open) and never create schema versions.

## [0.7.1] - 2026-11-26

Censor gains the Standard tier (run state and data), dataset audits without a project, and
a quick-tier warning for a pipeline that has stopped writing. All 14 packages move to
`0.7.1`; internal pins stay `>=0.7,<0.8`. No state migrations. Censor stays experimental.

### ⚠️ Upgrade notes

- `censor audit` (and `arsenal eval`) on the quick tier now runs `operability.freshness`.
  A project whose newest output is older than a week, or than `--max-age-hours`, gets a
  warning and can score a lower grade than under 0.7.0.
- `censor audit` exits 2 instead of 1 when `arsenal.yaml` cannot be read, and
  `censor report` exits 2 instead of 0 for a directory that does not exist. CI that reads
  these exit codes needs updating.

### Added

- Censor catches a stopped pipeline on the quick tier: `operability.freshness` compares each
  pipeline's newest output file time (or its state file, for a remote sink) with
  `--max-age-hours`, or a week when none is given, and warns past it. It reads file times
  only. The quick tier now accepts `--max-age-hours`.

- Censor Standard tier: `censor audit [DIR] --tier standard` also reads run state and data,
  and scores three more dimensions. Reliability: failed or quarantined units, the share of
  rows set aside by validation, whether output remains after a failure. Freshness: the last
  completed unit, the `arsenal dataset` publication state, and whether a limit is set
  (`--max-age-hours`). Data health: row count, key uniqueness and NULLs, NULL ratio per
  column, and the period a time column covers. A pipeline that never ran is not scored.
- Censor audits a dataset without a project: `--files GLOB`, `--duckdb FILE --table T`, or
  `--table T --postgres-dsn-env VAR`, with `--key` and `--time-column`. The DSN is read from
  the named variable and never printed.
- Scale guard: data over 2,000 files, 5 GB or 50 million estimated rows is not profiled; the
  report and stderr say how to narrow it with `--sample`, `--since` or `--full`.
  `--memory-limit` (default 4GB) and `--timeout` (default 120 seconds per query) bound a read.
- The report gains `sample`, `since` and `notices` (`schema_version` stays 1), and the
  Markdown report a "Scale and sample" section.
- `arsenal_core.errors.MissingEnvError` (a `FatalError`) for a spec that names an unset
  environment variable.

### Changed

- Censor reads only: run state is read from a copy under `.arsenal/censor/tmp/`, DuckDB files
  and Postgres are attached read-only. Nothing is written outside `.arsenal/censor/`
  (DuckDB downloads its `postgres` extension once into `~/.duckdb/extensions`).
- `censor audit` exits 2 when `arsenal.yaml` cannot be read (it was a failed check, exit 1),
  and `censor report` exits 2 for a directory that does not exist (it printed nothing, exit 0).
- `arsenal eval --help` shows the options it forwards to `censor audit`.

### Fixed

- Censor Standard: `--max-age-hours` was ignored for a project target, so a pipeline past its
  limit still passed `freshness.last_success`. With no limit, a last success older than a week
  now warns instead of passing.

- Censor: a save that fails part-way no longer leaves a `runs/<id>.parquet` without its JSON;
  two audits of one project at the same time no longer share a temp file; a commented-out
  `schedule` in a DAG file no longer counts as a schedule.

## [0.7.0] - 2026-11-25

A 14th package, Censor (`de-censor`, experimental), audits a project from its files and
grades it. All 14 packages move to `0.7.0` and internal pins move to `>=0.7,<0.8`, so
upgrade them together. No existing command, option, spec field or state file changes,
and there are no state migrations. Upgrade steps: [docs/upgrade-0.7.md](docs/upgrade-0.7.md).

### Added

- Censor (`de-censor`, new package): `censor audit [DIR] --tier quick` grades a DE Arsenal
  project from its files alone, without running it. Five dimensions are scored 0-10 and
  combined into a 0-100 score and a grade (A+ to F): correctness (the manifest and every
  spec load and compile, as in `arsenal check`), quality guards (`validate`,
  `on_violation`, key rules, `schema_drift`, `contract`), security (literal secrets in
  spec files), operability (README, a schedule, lineage) and agent readiness (CLAUDE.md,
  the de-arsenal plugin, a deny rule for `.arsenal/`, a secret-scan hook). A literal
  secret or a spec that does not load caps the grade at C. Every failed check names its
  evidence and a fix; a secret's value is never printed.
- `censor report` lists saved runs and `censor compare <a> <b>` shows the checks whose
  status changed. Each audit is saved under `.arsenal/censor/` as JSON, Parquet (one row
  per check, queryable with `gladius query`) and a Markdown report (`--lang en|ko`).
- `arsenal eval` runs `censor audit` with the same arguments. It needs `de-censor`
  installed (`pip install de-censor`); `de-arsenal` does not depend on it.
- `schemas/censor-report.json`: the JSON Schema of the report `censor audit` prints to
  stdout. Exit codes: 0 no failed check, 1 at least one, 2 Censor could not run.

## [0.6.17] - 2026-11-25

Augur's read-only guard is enforced by the DuckDB parser and a sandboxed connection
instead of a first-word regex. All 13 packages move to `0.6.17`; internal pins stay
`>=0.6,<0.7`. No state migrations.

### Security

- Augur: the read-only guard no longer trusts the first word of the generated SQL.
  `is_select()` now parses with DuckDB and accepts exactly one SELECT statement, so
  `-- comment` + `COPY`, `SELECT 1; COPY ...`, `EXPLAIN ANALYZE COPY ...` and comment +
  `ATTACH` are rejected as `NON_SELECT` (all four wrote files in 0.6.16). Generated and
  golden SQL now run on a connection limited to the catalog's folders
  (`allowed_directories`, `enable_external_access=false`, locked configuration, no
  extension auto-install), so files outside the lake and `https://` paths fail with a
  Permission Error. `de-augur` now requires `duckdb>=1.3`.

## [0.6.16] - 2026-11-25

Augur works with Claude 5.x models (0.6.15 sent `temperature`, which they reject
with a 400), and `pugio transfer` arrives as an experimental command. All 13 packages move
to `0.6.16`; internal pins stay `>=0.6,<0.7`. No state migrations.

### Added

- `pugio transfer run|status` (experimental): a Hive or Redshift query is exported by the
  source engine straight to S3 as Parquet (`INSERT OVERWRITE DIRECTORY` / `UNLOAD`), then
  copied into Redshift with `COPY`, so large results never pass through `pugio`. `split`
  cuts the query into date or integer ranges that are loaded and resumed one at a time; a
  unit whose load failed reuses its export on the next run. `target.mode` is `replace`
  (swap the range) or `merge` (swap by key). Before exporting, the query's column names
  and type families are compared with the target table's, position by position, because
  COPY loads Parquet by position (`check_types: false` compares names only,
  `check_columns: false` turns both off). `--start`/`--end` narrow the split for one
  run; `examples/transfer/airflow/` runs a transfer as a daily Airflow 3 DAG that way. New
  extra `hive` (`impyla`). See `examples/transfer/`.

### Changed

- Augur's default Anthropic model is now `claude-sonnet-5-5` (was `claude-sonnet-4-6`).
  `--model` still overrides it.

### Fixed

- Augur no longer sends `temperature` to the Anthropic API. Claude 5.x models reject the
  parameter with a 400, so every `augur ask` and `augur eval` call against them failed on
  the first request.

## [0.6.15] - 2026-11-24

Installing `de-arsenal` now also puts `pugio` and `gladius` on PATH. All 13 packages move
to `0.6.15`; internal pins stay `>=0.6,<0.7`. No state migrations.

### Fixed

- `uv tool install de-arsenal` (and pipx) put only `arsenal` on PATH, so `pugio status` and
  `gladius compile`, which the docs use, were "command not found". `de-arsenal` now declares
  the `pugio` and `gladius` commands too. They run the same CLIs as before and stay
  available as `arsenal collect …` and `arsenal transform …`. With pip, uninstalling
  `de-arsenal` alone also removes those two command files; reinstall `pugio` or
  `de-gladius` if you keep using them without it.

## [0.6.14] - 2026-11-24

Fixes from testing 0.6.13 against an internal repository: simultaneous first runs, a
renamed column slipping past `schema_drift: block`, and `split` rows not raising the
alert. All 13 packages move to `0.6.14`; internal pins stay `>=0.6,<0.7`. No state
migrations.

### ⚠️ Upgrade notes

- With `on_violation: split`, `arsenal status` now exits 1 while rows sit in the DLQ
  (`rejected_rows` > 0). Retry or delete the reviewed DLQ files to clear it.
- With `schema_drift: block` (or `warn`) on a file source, a run now fails (or warns)
  when its files do not all have the same columns. Before, only the first file of a run
  was compared. Folders that mix layouts on purpose need `allow`, or one pipeline per
  layout.

### Fixed

- Two runs started at the same instant on a fresh project no longer crash in the collect
  step with `FileNotFoundError` from `register_stream_writer`. Both wrote the same
  temporary file, so the second rename found nothing. Each process now uses its own
  temporary file, and a registration another process finished first is accepted.
- `schema_drift: block` now catches a column added, removed or renamed in any file of a
  run, not only the first one. Before, a renamed column in a file read after another new
  file loaded silently and `arsenal status` exited 0. File sources only; within a run each
  file is compared with the first, and `warn` logs the difference.
- Rows that `on_violation: split` set aside now raise the alert. The unit is `done`, so
  `arsenal status` reported `alert: false` while rows sat in the DLQ. Its `pipelines`
  block has a new `rejected_rows` count and alerts while it is above 0; `pugio status`
  prints `rejected_rows: N`. `pugio dlq retry` after fixing the source, or deleting the
  reviewed DLQ files, clears it.
- A drift error on a column that was just pinned in `source.column_types` now says what
  to do: files loaded before the pin keep the old type in the output, so the state file
  and the output must be deleted and everything reloaded. The error itself is unchanged:
  accepting the new type would mix two types in one column.

## [0.6.13] - 2026-11-24

Two fixes from testing 0.6.12: overlapping runs no longer crash in the transform step,
and deleting a file that failed to load clears the alert. All 13 packages move to
`0.6.13`; internal pins stay `>=0.6,<0.7`. No state migrations.

### ⚠️ Upgrade notes

- A unit can now be in the new state `withdrawn`. `pugio status` prints it as
  `withdrawn: N` and `arsenal status` counts it under `units` without raising an alert.
  A script that expects only `done`, `failed`, `quarantined`, `pending` and `running`
  should accept it.
- A transform leaves a `<output>.lock` file next to its output directory (for example
  `data/clean.lock`). It is empty and safe to ignore; do not delete it while a run is
  going.

### Fixed

- Two `arsenal run` (or `gladius run`) processes that reach the same transform together
  no longer crash with `FileNotFoundError: 'data/clean.tmp'`. The collect step already
  let only one run through, but once it was done both went on to the transform and shared
  its temporary directory. A transform now holds an OS lock on `<output>.lock` for the
  whole run; the second one stops with "transform ... is busy". The lock is released
  when its process exits, `kill -9` included, so a crash never leaves it behind.
- Deleting a file that failed to load now clears the alert. The failed (or quarantined)
  unit stayed `failed` until a file with the same name loaded, so `arsenal status` kept
  exiting 1. After a run that reads the whole folder without a failure, such units become
  `withdrawn`, keeping their last error; a file that comes back under that name is loaded
  again. With `detect_changes`, the same applies to the old content of a file that was
  replaced.

## [0.6.12] - 2026-11-24

`arsenal status` now watches the batch pipelines under `pipelines:`, and two runs started
together on a fresh project no longer crash while creating the state database. All 13
packages move to `0.6.12`; internal pins stay `>=0.6,<0.7`. No state migrations.

### ⚠️ Upgrade notes

- `arsenal status` now exits 1 when a pipeline under `pipelines:` has `failed` or
  `quarantined` units. A script that ran it only for its output and stops on a non-zero exit
  (`set -e`, a CI step) will now stop there. It does not check how old a pipeline's last
  success is; keep `pugio status <spec> --max-age-seconds <n>` for that.

### Fixed

- `arsenal status` now reports the batch pipelines listed under `pipelines:` in a new
  `pipelines` block (unit counts, last completion). A pipeline with `failed` or
  `quarantined` units raises `alert` and exits 1. Before, only streams, distributed runs,
  datasets and the catalog were checked, so a failed collect still printed `alert: false`.
  A pipeline that has never run reports `never-run` and gets no state file.
- Two runs started at the same moment on a fresh project no longer crash with
  `sqlite3.OperationalError: database is locked` (or `table ... already exists`) while
  creating the state database. Switching the new file to WAL is retried, each migration
  re-reads the schema version under its write lock, and a lock that never clears becomes
  a one-line "state database ... is busy" error. The second run then gets the usual
  "pipeline ... is busy" message or skips the units the first one finished.

## [0.6.11] - 2026-11-23

DuckDB 2.0 compatibility: a fresh install resolves DuckDB 2.0, and 0.6.10 printed a
`UserWarning` on every Postgres publish with it. All 13 packages move to `0.6.11`; internal
pins stay `>=0.6,<0.7`. No state migrations.

### Fixed

- A fresh install resolves DuckDB 2.0, which the lock file had not been testing. With it,
  `arsenal dataset apply --postgres-table` and the postgres/mysql `DatabaseSource` printed
  `UserWarning: Arrow output on a DB-API result makes an extra copy of the rows` on every run.
  They now read through a relation, which also avoids that extra copy.
- `gladius query`, `arsenal query` and `augur report` render their table on a connection of
  their own instead of DuckDB's process-wide default one, which DuckDB 2.0 refuses to reuse
  while an earlier result on it is unfinished.
- CI runs the suite once more on the newest versions our dependency ranges allow
  (`latest-deps`, on every PR and weekly), not only on the versions pinned in `uv.lock`.

## [0.6.10] - 2026-11-23

`arsenal dataset` can publish a reviewed snapshot to a Postgres table as well as to
`.arsenal/dataset.duckdb`. All 13 packages move to `0.6.10`; internal pins stay `>=0.6,<0.7`.
No state migrations.

### Added

- `arsenal dataset apply --postgres-table schema.table --postgres-dsn-env VAR` also publishes the
  snapshot to a Postgres table, so others can query a reviewed ledger in the company database.
  The table's rows are replaced in one transaction (DELETE + INSERT), so views and grants on it
  survive and readers see the previous snapshot until commit. A Postgres failure fails the apply
  and the DuckDB publication rolls back with it. The schema is never created, and an existing
  table with other columns or types is refused. Later runs reuse the saved target. Needs the
  `postgres` extra.

## [0.6.9] - 2026-11-23

Fixes from running DE Arsenal against Excel ledgers at work: `arsenal dataset` reads Excel,
the file source accepts `.xlsb`, and a wrong database password stops at once instead of
retrying. All 13 packages move to `0.6.9`; internal pins stay `>=0.6,<0.7`. No state
migrations.

### Added

- `arsenal dataset` reads Excel workbooks (`.xlsx`, `.xlsm`, `.xlsb`, `.xls`). The first sheet
  is read as text, like CSV, so leading zeros and whole numbers compare as they look in Excel.
  `~$` lock files that Excel leaves beside an open workbook are skipped. Install the new
  `excel` extra: `uv tool install "de-arsenal[excel]"`.
- The file source auto-detects `.xlsb` and `.xlsm` workbooks as Excel. The `excel` extra
  (fastexcel) already read them, but the suffix check refused them first.

### Fixed

- `examples/real-world/d1-snapshot/d1_snapshot.py` read each page from the start of the table.
  Its paging predicate could not use the key index, so an export of n rows cost about
  n × pages / 2 row reads at D1 instead of n. It now seeks by key and prints the request and
  row-read counts D1 reports. Measured on a 105,484-row table: 106 requests, 105,484 rows read.
- A postgres or mysql `DatabaseSource` with a wrong password, a rejected `pg_hba.conf` entry,
  or a missing database or role now fails at once with a fatal error. DuckDB raises these as
  IO errors, so they used to be retried until the backoff ran out.

## [0.6.8] - 2026-11-22

A fix for JSONL snapshots found on a production Cloudflare D1 table, and an example for
publishing a D1 table. All 13 packages move to `0.6.8`; internal pins stay `>=0.6,<0.7`.
No state migrations.

### Fixed

- `arsenal dataset` no longer refuses a JSONL snapshot because a column is NULL in every row.
  JSON types are inferred from values, so such a column arrived with a placeholder type and the
  apply failed as a schema change although only values had changed. The column now takes the
  published type (or, when the published column was the empty one, the incoming type), and the
  difference is reported as updated rows. The same applies between the files of one snapshot.
  A column that has values on both sides and different types is still refused.

### Added

- `examples/real-world/d1-snapshot`: a read-only script that writes a Cloudflare D1 table as a
  snapshot for `arsenal dataset`, with the record of a run against a production table.

## [0.6.7] - 2026-11-22

- Preview and publish CSV, JSONL and Parquet snapshots with `arsenal dataset`.
  Saved rules support key-based comparisons, event-time ranges and freshness checks.
  A failed apply retains the last successful publication.
- Aquila discovers dataset publications and renders the current output and latest
  attempt separately in JSON and Mermaid lineage. Opening an Aquila catalog
  upgrades its schema to v3. Back up the catalog before opening it if you need to
  use it with an older Aquila release.
- Build the portable Agent Skills ZIP with `python3 scripts/build_skill.py`.
  The Claude plugin and portable skill share the collection and operations guides.


## [0.6.6] - 2026-11-22

A check for hand-edited specs, a freshness check that fails when it should, and agent skills.
All 13 packages move to `0.6.6`; internal pins stay `>=0.6,<0.7`. No state migrations.

### ⚠️ Upgrade notes

- `pugio status --max-age-seconds` now exits 1 when the pipeline is stale. A script that ran it
  only for its output and stops on a non-zero exit (`set -e`, a CI step) will now stop there.

### Added

- `arsenal check` validates `arsenal.yaml` and every pipeline and transform it lists, reports all
  broken files in one pass and exits 1 if any are invalid. It runs nothing and writes nothing.
  A file source whose path matches no files is reported as a warning.
- Agent skills plugin under `plugins/de-arsenal` (`start`, `ingest`, `transform`, `ask`, `trust`,
  `operate`, `doctor`) with a marketplace manifest.

### Fixed

- `pugio status --max-age-seconds` exits 1 when the pipeline is stale. It printed the alert and
  exited 0, so a scheduled freshness check never fired. `examples/deploy/README.md` already
  described the exit code as 1.

## [0.6.5] - 2026-11-18

Two ways to make a pipeline's output readable from Trino. All 13 packages move
to `0.6.5`; internal pins stay `>=0.6,<0.7`. No state migrations and no change
to existing behaviour.

### Added

- **Iceberg sink** (`type: iceberg`, `pugio[iceberg]` / `de-arsenal[iceberg]`,
  experimental), written with pyiceberg to any catalog it supports (REST, Glue,
  Hive, SQL). With `merge_key`, rows are upserted; without it, batches are
  appended and a batch already committed for the same unit is not appended
  again. The table is created from the first batch and later columns are added
  to it. Tested against real Iceberg tables (SQLite catalog, local warehouse);
  not verified against a REST or Glue catalog, S3, or Trino itself.
- **`pugio ddl <spec>`** prints `CREATE TABLE ... WITH (external_location,
  format = 'PARQUET')` for a Trino Hive external table over a Parquet sink,
  from the schema the pipeline last recorded. It prints only; the statement has
  not been run against Trino.

## [0.6.0] - 2026-11-18

Minor release: a 13th package, **Pilum** (`de-pilum`, experimental reverse ETL),
three more fixes for input that used to be lost or accepted without a word, and
a first-run path that works as the README prints it. All 13 packages move to
`0.6.0`; internal pins move to `>=0.6,<0.7`. No state migrations: StateStore
stays v3 and `aquila.db` stays v2. Upgrade steps: [docs/upgrade-0.6.md](docs/upgrade-0.6.md).

### ⚠️ Upgrade notes

- **A file source whose `path` matches no files is now an error.** It used to
  finish with `fetched=0`. Set `source.allow_empty: true` for a drop folder that
  is sometimes empty.
- **CSV columns with leading zeros now load as strings.** A column holding
  `02139` or `007` was inferred as an integer and stored as `2139` and `7`. If
  such a column really is numeric, pin it: `column_types: { seq: int64 }`.
  Downstream `cast` steps and Postgres/DuckDB sink tables built on the old
  integer type need the same decision.
- **Pugio warns when files of one run disagree on a column's type**, and fails
  the run under `schema_drift: block`. Pin the type with `source.column_types`.
- **The Postgres sink stores a timestamp without a time zone as `timestamp`**
  instead of refusing it. Timestamps with a zone are `timestamptz` as before.
- **Status labels changed.** Onager, Scutum, Scorpio and Spatha are listed as
  beta instead of stable. No code or API changed with the label.

### Added

- **Pilum** (`de-pilum`, `pilum` CLI, `de-arsenal[dispatch]`): sends records
  from a Parquet dataset or a DuckDB query to an HTTP endpoint or a Slack
  webhook, and sends each change once. `insert` / `upsert` / `mirror` modes, a
  per-dispatch ledger written only after a 2xx, idempotency-key header,
  retry with backoff, a dead-letter queue with `replay`, a first-run size limit
  and a delete-share limit. Delivery is at-least-once unless the endpoint
  deduplicates on the idempotency key. The ledger is its own SQLite file, not a
  StateStore migration (PL-ADR-3). Not included: bulk requests, OAuth refresh,
  `arsenal run` integration. See [packages/pilum/README.md](packages/pilum/README.md).
- **Redshift sink** (`type: redshift`, `pugio[redshift]` / `de-arsenal[redshift]`,
  experimental): staged delete + insert on `merge_key` in one transaction.
  `load: insert` sends multi-row INSERTs; `load: copy` stages each batch as
  Parquet under `staging_path` and loads it with `COPY` and an IAM role. Not
  verified against a live cluster: the insert path is tested on PostgreSQL, the
  copy path through the statements it issues. Pipelines only, not streams.
- **`validate.on_violation: split`**: load the rows that pass and write only the
  failing rows to the DLQ. `quarantine` sets a whole page or file aside, so data
  with a few bad rows in every unit loads nothing (NYC taxi 2024 Q1: 1.2% bad
  rows, 0 of 9.55M rows loaded). The unit is `done`; `pugio run` reports
  `rejected_rows` and `pugio dlq list` shows what was set aside. The default
  stays `quarantine`.
- `source.column_types` for `type: file` (CSV): pin a column to `string`,
  `int64`, `float64`, `bool`, `date` or `timestamp`.
- `source.allow_empty` for `type: file`.
- `pagination.stop_on: empty_page` for `offset` and `page` modes. The default,
  `short_page`, stops at the first page with fewer than `size` rows; if the
  server caps its page size below `size`, that is the first page. `empty_page`
  costs one extra request and does not depend on the cap.
- `source.detect_changes` for `type: file`: also key a file on its content, so
  a file that is overwritten under the same name (a daily `export.csv`) is
  ingested again. Off by default, where a file is ingested once by its path.
  Refused with a Parquet sink, which would keep both versions.
- `examples/deploy/`: a GitHub Actions workflow, a crontab line and a
  Dockerfile for running a project on a schedule, each keeping the state
  directory and posting to Slack on failure. Syntax-checked only.
- Tests that need Postgres use an existing server when
  `ARSENAL_TEST_POSTGRES_URL` is set (one fresh database per test module), so
  they run where Docker does not.
- **Augur works with OpenAI-compatible APIs.** `--provider openai` reads
  `OPENAI_BASE_URL` (the OpenAI SDK convention), so Gemini, Groq, OpenRouter or a
  local server can run `augur ask` and `augur eval`. The key stays in
  `OPENAI_API_KEY`.
- `docs/11-real-data-experiments.md` and `experiments/real-data/`: results and
  reproducible specs from running the tools on real APIs and data.
- The `csv-cleanup` recipe ships `input/sample.csv`, so `arsenal init
  csv-cleanup && arsenal run` works without adding data.
- `docs/adr/` PL-ADR-1..5, `docs/README.md` (index of the design documents),
  `schemas/dispatch.json` and its generated reference.

### Fixed

- **`arsenal init csv-cleanup` followed by `arsenal run` failed** with a raw
  DuckDB `IO Error` and left an empty `data/clean.tmp` directory: the recipe had
  no input and the collect step reported success with zero rows.
- **A CSV with a date, a naive timestamp, a small integer or an all-empty
  column could not be loaded into Postgres** (`unsupported arrow type`). The
  sink now maps `date`, `time`, `timestamp`, `smallint`/`integer`, `real`,
  `numeric`, `bytea`, and creates an all-empty column as `text`.
- A transform whose input has no Parquet files now says which input is empty
  instead of printing the compiled SQL, and removes its temporary directory.

### Changed

- The READMEs are rewritten around what the tools do and what has been
  measured. Design-document links moved to `docs/README.md`.
- `arsenal init` copies a recipe's subdirectories, not only its top-level files.

## [0.5.3] - 2026-11-18

Patch release: two silent data-loss bugs found by running the tools against
real data (the GitHub API and the public airport-codes CSV). Neither raised an
error. All 12 packages move to `0.5.3`; internal pins stay `>=0.5,<0.6`. No
state migrations.

### Fixed

- **The `github-issues` recipe collected only its first page.** The recipe,
  `examples/github-issues.yaml` and the Pugio README used
  `mode: offset, param: page`. Offset mode sends `0, size, 2×size, …`, so a
  page-numbered API was asked for page 0 and then page `size`, and the run
  reported success after one page (6 of 21 issues in a real run with
  `per_page=5`). They now use `mode: page`.
- **CSV cells reading `NA` could turn into nulls.** The file source used
  pyarrow's default null tokens (`NA`, `N/A`, `null`, …). When every value of
  a column in one file was such a token, the column was inferred as type
  `null` and the values were lost, so the same data loaded differently
  depending on how it was split into files (39,842 North American airports lost
  their `continent` when the CSV was split by continent). Only an empty cell
  is null now.

### Added

- `source.null_values` for `type: file`: the CSV cell values read as null
  (default `[""]`). Set `["", "NA", "N/A"]` to restore the old tokens for a file
  that really uses them as null markers.

### ⚠️ Upgrade notes

- **`mode: offset` with a page-numbered `param`** (`page`, `page_number`,
  `pageNo`, …) is now a spec validation error that points at `mode: page`.
  Such a spec was already skipping pages.
- **CSV columns holding `NA`/`N/A`/`null` now load those as strings.** A numeric
  column that used `NA` for missing values becomes a string column unless
  `null_values` lists the token.

## [0.5.2] - 2026-11-17

Patch release: two correctness fixes (a Pugio run racing Onager compaction, a
Hasta token rotation duplicating a batch), continuous Hasta polling, and the
Aquila follow-ups. All 12 packages move to `0.5.2`; internal pins stay
`>=0.5,<0.6`. The only state change is `aquila.db` v1 → v2, migrated in place
on first open; pipeline state DBs are untouched.

### ⚠️ Upgrade notes

- **Hasta streams with `headers` get a new revision once.** Header values (tokens)
  are no longer part of the revision, so the first run after upgrading plans
  batches under new identities. Let each such stream commit its in-flight batch
  first (`hasta checkpoint show` reports no `planned` batches), then upgrade.
- **A Pugio run now refuses to start while another run of the same pipeline, or
  an Onager compaction of its Parquet output, holds the pipeline lease.** A
  hard-killed run's lease expires within 60 seconds.

### Added

- Hasta `cursor.on_null: last_event_id`: a feed that answers `next: null` when
  caught up is polled continuously from the last committed event id instead of
  ending for good. Empty caught-up polls share one ledger row.
- Aquila warns when two specs write the same location; drops the edges and
  contracts of spec files deleted from a scanned directory (`aquila.db` v2,
  migrated in place); and, opt-in, records remote Parquet footers (`--remote`)
  and every schema of Iceberg tables (`--iceberg-uri`, `de-aquila[iceberg]`).
- `docs/adr/` with AQ-ADR-1..4.

### Fixed

- **Onager could compact a Pugio Parquet sink mid-run** (Hasta streams were
  already fenced). Pugio runs now hold a heartbeat-renewed pipeline lease and
  register as writers of their local Parquet sink.
- **Rotating a Hasta API token could duplicate a batch.** The revision included
  substituted header values, so a written-but-uncommitted batch replayed under a
  new identity after a rotation. (`${ENV_VAR}` in headers already worked; the
  limits doc wrongly said otherwise.)

### Changed

- CI actions moved to their Node 24 majors (checkout v7, setup-java v6,
  upload-artifact v7, download-artifact v8); setup-uv, which no longer
  publishes moving major tags, is pinned to the v10.3.3 commit.

## [0.5.1] - 2026-11-17

Patch release: one data-correctness fix. All 12 packages move to `0.5.1`
together; internal pins stay `>=0.5,<0.6`, so `0.5.0` installs pick it up on
upgrade. No state migration, no behaviour change other than the fix below.

### Fixed

- **`arsenal run` never reprocessed new data for `distributed:` stages with
  `input_version: auto`.** The run id was derived from the literal string
  `auto`, so every later run found the first (already succeeded) Ballista run
  and only reconciled it — silently leaving the output stale. Arsenal now
  resolves `auto` to the input's listing fingerprint first, keys the run by it
  and passes that fingerprint to `ballista run`: unchanged input reconciles the
  same run, changed input submits a new one. `arsenal status` resolves the same
  way and reports `input-unresolved` when the input cannot be listed.

## [0.5.0] - 2026-11-17

Catalog release: a 12th package, **Aquila**, records what data exists, where it
came from and whether it still matches its contract — without any other tool
depending on it. All 12 distributions ship in lockstep with internal pins moved
to `>=0.5,<0.6`. No state migration. Upgrade notes:
[docs/upgrade-0.5.md](docs/upgrade-0.5.md).

### ⚠️ Upgrade notes

- **Pins move to `>=0.5,<0.6`.** Install the whole set together.
- **StateStore is unchanged (schema v3).** 0.5 neither migrates nor backs up any
  state DB; rolling back to 0.4 is a reinstall.
- **`arsenal status` can now exit 1 on contract findings** — only when
  `de-arsenal[catalog]` is installed and `.arsenal/aquila.db` exists.

### Added

- **`de-aquila` (12th package, preview).** A file-backed catalog: `aquila scan`
  reads Pugio/Gladius/Ballista/Hasta specs, the tools' state databases
  (read-only — never migrated, locked or backed up) and local Parquet footers
  into `.arsenal/aquila.db`. Datasets are keyed by normalized location, so
  `out/`, `out/*.parquet` and `./out` are one dataset whichever tool names it.
  Schema versions with diffs (`history`), cross-tool dataset lineage with cycle
  detection and Mermaid output (`lineage`), contract hash tracking and checks
  against the latest schema (`show`), tags, descriptions, deprecation warnings
  for every reader and FTS5 search. Rescans are idempotent. Nothing depends on
  it (tested in both directions).
- `augur index --aquila PATH` (and automatically `.arsenal/aquila.db` when no
  sources file is given) reads table locations from Aquila through read-only
  sqlite, so `de-augur` gains no dependency. The 34-case eval gives identical
  per-case outcomes with either source.
- `arsenal status` gains a `catalog` block — totals, contract findings,
  deprecation warnings — with the `de-arsenal[catalog]` extra.

## [0.4.0] - 2026-11-17

P2 release: two new execution tools (Hasta streaming/CDC, Ballista
distributed transforms) on a shared, engine-neutral execution contract, plus
the experimental Augur package. 11 distributions now ship in lockstep with
internal pins moved to `>=0.4,<0.5`. Upgrade notes:
[docs/upgrade-0.4.md](docs/upgrade-0.4.md).

### ⚠️ Upgrade notes

- **StateStore schema v3 (forward-only).** The first `0.4` process that opens a
  `0.3` state DB migrates it (v1 → v3: additive tables plus one ledger index)
  and first writes a backup next to it (`backups/<name>-v<old>-<stamp>.sqlite`).
  A DB written by a newer version is refused, never downgraded. Rollback to
  `0.3` = stop all runners and restore that backup.
- **Pins move to `>=0.4,<0.5`.** Install the whole set together; `0.3.x`
  installs will not pick this up automatically.
- **`onager compact` may now refuse.** A dataset that a Hasta stream writes to is
  compacted only while no runner holds its lease and no batch is
  written-but-uncommitted (`DatasetBusyError`). Batch-only datasets are
  unaffected.

### Added

- **`de-hasta` (10th package).** Leased polling microbatch streaming from cursor
  REST APIs and Postgres outbox CDC (`I`/`U`/`D` envelopes ordered by
  `sequence`, applied transactionally to DuckDB/Postgres with an event-id
  ledger). Each batch is written before its fenced checkpoint advances, so a
  crash replays at most the uncommitted batch under the same identity —
  100,000 events through 12 crashing runners arrive exactly once
  (`tests/test_resume_100k.py`). Operations: `hasta checkpoint show|reset`
  (`hasta status` alias, health block, `--max-lag-seconds`), graceful
  `SIGINT`/`SIGTERM` stop, a runnable quickstart under `examples/hasta`.
  Throughput ceiling ~334k events/s in-process on an M3 Pro, network
  excluded ([benchmarks/RESULTS.md](benchmarks/RESULTS.md)).
- **`de-ballista` (11th package).** Spark-on-Kubernetes execution of a portable
  Gladius plan with Iceberg as the only accepted production output: one tagged
  snapshot per logical run, retries reuse it, input versions pinned by Iceberg
  snapshot id or Parquet listing fingerprint. `ballista plan --probe` checks the
  live environment (spark-submit, API `/readyz`, driver RBAC, staging
  create-only writes, Iceberg catalog, digest-pinned image). The base install
  has no Spark/Kubernetes/Iceberg dependency (`spark-kubernetes`, `iceberg`,
  `object-store` extras). Verified in CI by a DuckDB↔Spark golden corpus and a
  kind + MinIO + Iceberg REST end-to-end run.
- **Engine-neutral execution contract (`arsenal-core`).** Execution
  models/capabilities, canonical hashing, output proofs, fenced leases, run
  records and a stream checkpoint/batch ledger in StateStore;
  `arsenal_core.writers` lets maintenance tools take every registered stream
  writer's lease (`exclusive_dataset`).
- **Gladius portable `LogicalPlan` v1** with DuckDB and Spark renderers.
  `compile_sql()` stays as a compatibility wrapper; raw `sql` and Python UDF
  steps remain local-only and are rejected by portable preflight. `concat()` is
  not portable (NULL semantics differ) — use `||`.
- **Pugio sink capability contracts** and CDC apply sinks used by Hasta.
- **Scorpio stream health and run summaries.** `assess_stream`
  (healthy/lagging/stalled/failing/unknown with reasons) and `summarize_runs`.
- **Spatha built-in actions.** `command` tasks (argv, no shell, output kept out
  of errors) and `ballista_reconcile`, so dependents start only after a
  distributed run succeeds.
- **`arsenal.yaml` `streams:` and `distributed:` stages** behind the
  `de-arsenal[streaming]` / `de-arsenal[distributed]` extras, run in order
  collect → stream → transform → distributed; re-running reconciles the same
  Ballista run. `arsenal status` prints stream health and run state as JSON and
  exits 1 when something needs attention. A missing extra fails before
  anything runs, with the install command.
- **`de-augur` (9th package, experimental).** A natural-language entry point on
  top of the Parquet lake: schema retrieval (lexical, over table/column names and
  sample values — no vector DB) → LLM (Anthropic or OpenAI over plain `httpx`, no
  SDK) → SELECT-only guard → DuckDB. Every call is traced to JSONL. `augur eval`
  runs a YAML case file against golden SQL and classifies each case into one of
  nine outcomes (`RETRIEVAL_MISS`, `NON_SELECT`, `UNANSWERABLE`,
  `HALLUCINATED_TABLE`, `HALLUCINATED_COLUMN`, `EXEC_ERROR`, `WRONG_SHAPE`,
  `WRONG_RESULT`, `CORRECT`), writing results as Parquet so `gladius query` can
  aggregate them. Ships 34 cases over a seed-fixed synthetic fixture, the
  two-week analysis protocol ([docs/10-augur-eval-protocol.md](docs/10-augur-eval-protocol.md))
  and an article template. Live API calls are not yet exercised in CI.

### Fixed

- Hasta source errors no longer leak credentials: HTTP failures become
  classified `FatalError`/`RetryableError` lines with the URL's password and
  query values masked (previously a raw `httpx` traceback with the full URL),
  and Postgres outbox errors mask the DSN.

## [0.3.0] - 2026-08-14

Correctness and hardening pass. Every item below was found by adversarially
exercising the shipped code (not by review) and is pinned by a regression test
that fails the way the original defect failed.

### ⚠️ Breaking

`offset`/`page` REST pipelines change their unit ids (see the `unit_key` entry
below). An existing state DB will treat every unit as new and re-collect the
whole source, and a parquet sink will keep the files written under the old ids
alongside the new ones — i.e. duplicated rows in the dataset.

**Migration:** before the first run on `0.3.0`, clear the pipeline's
`state_dir` and its sink output directory, then re-run. `cursor`/`link`
pipelines and non-REST sources are unaffected. Cross-package pins move to
`>=0.3,<0.4` so `0.2.x` installs will not pick this up automatically.

### Fixed

- **Statement injection through `filter`/`derive`/`map` expressions.** These
  expressions are interpolated into the `COPY (<compiled SQL>) TO ...` template
  that `gladius run` executes, with no validation, so a `;` let a spec close the
  wrapper and run a second statement — reproduced end to end, writing an
  attacker-chosen file while the run reported success. The `sql` step already
  rejected `;`; the same rule now applies to every expression slot, and each
  interpolation site is parenthesised so a stray `)` is a parse error instead of
  a scope escape. Expression *contents* are still unvalidated by design (the dbt
  trust model) — the residual boundary is now written down in
  [docs/08-limits.md](docs/08-limits.md).
- **`offset`/`page` unit keys ignored the page size.** `unit_key` was
  `offset={n}`, so changing `pagination.size` produced the same `unit_id` for a
  different row range: the rerun skipped it as already done and state silently
  diverged from the data. `docs/02-architecture.md` had specified
  `offset=200:limit=100` from the start — the implementation had drifted.
  **Breaking for `offset`/`page` pipelines:** unit ids change, so an existing
  state DB re-collects everything and a parquet sink keeps the old-id files
  alongside the new ones. Clear `state_dir` and the output directory before
  rerunning.
- **Concurrent parquet writes could publish a corrupt file.** The temp path was
  the deterministic `{unit_id}.parquet.tmp`, so two writers of the same unit
  interleaved into one file before each called `os.replace` — the rename is
  atomic but the bytes it published were not. Temp names now carry pid and a
  random suffix, and a failed write no longer leaves an orphan `.tmp`.
  The regression test then surfaced a second, Windows-only half of the same
  problem: Windows refuses to rename onto a destination another handle holds
  open, so concurrent writers of one unit hit `PermissionError` where POSIX
  succeeds. The replace is now retried briefly and, if contention persists,
  raised as `RetryableError` so the runner absorbs it as the transient failure
  it is.
- **`unique` reported nulls as duplicates.** `pc.count_distinct` defaults to
  `mode="only_valid"`, so subtracting it from `len(col)` counted every null as a
  duplicate: a column of `[1, 2, None, None]` reported two violations. Under the
  default `quarantine` policy that silently sent clean batches to the DLQ. Nulls
  are now excluded from both sides, matching the null discipline `min`/`max`
  already documented.
- **YAML syntax errors escaped as raw tracebacks.** `load_pipeline` and
  `load_transform` called `yaml.safe_load` outside their `try`, so a syntax
  mistake — an unquoted `timestamp[s]` inside a flow mapping, say — surfaced as
  a multi-line pyyaml dump with a caret instead of a `FatalError`, even though
  the same functions already translated `OSError` and pydantic
  `ValidationError`. All three loaders (pipeline, transform, and the
  `arsenal.yaml` manifest) now emit one line: `invalid YAML in {path}:
  {context}: {problem} (line N, column M)`. The manifest loader was catching
  `yaml.YAMLError` already but formatting it with `str(e)`, which is the same
  multi-line dump. A decode failure on a non-UTF-8 spec is translated too. The
  shared formatter lives in the new `arsenal_core.yaml_io`.
- **Release workflow published sdist-only for a single package.** The selection
  glob `{name}-{version}.*` matches `.tar.gz` but not
  `-py3-none-any.whl`, and the existence guard passed on the sdist, so the
  manual bootstrap path shipped without a wheel and said nothing. Both artifact
  shapes are now selected and independently asserted. The tag-push path uploads
  all of `dist/` and was unaffected.

## [0.2.2] - 2026-08-13

Packaging fixes found while verifying `0.2.1` from a clean environment.

### Fixed

- `pugio` did not declare its runtime dependency on `de-scutum`. The uv
  workspace resolved it locally, so tests and CI were green while the published
  wheel was broken.
- `onager` and `scutum` were renamed to the `de-onager` / `de-scutum`
  distributions after the bare names turned out to be taken on PyPI. Import
  packages and CLIs are unchanged.

## [0.2.1] - 2026-08-13

### Fixed

- `scorpio` was renamed to `de-scorpio`. The bare name belongs to an unrelated
  PyPI project, so `pugio` 0.2.0 shipped declaring a dependency on a stranger's
  package. Distribution names are now verified against PyPI before release (see
  `docs/reference/packaging.md`).

## [0.2.0] - 2026-08-13

P1 platform release. The workspace packages now share the `0.2.0` version.
The transform package is published as `de-gladius`; its Python import package
and `gladius` CLI remain unchanged.

### Added

- Schema snapshot comparison and drift reporting in `arsenal-core`.
- Incremental transforms and Python UDF steps in Gladius.
- Object-storage and dlt source capabilities in Pugio.
- Onager compaction/backfill, Scorpio freshness checks, Scutum data contracts
  and locks, and Spatha workflow planning.
- Tag-driven release verification, distribution builds, and PyPI publishing.

### Changed

- The PyPI distribution name `gladius` is replaced by `de-gladius`; existing
  `import gladius`, `gladius` CLI, and source directory names remain stable.
- Published inter-package dependency ranges now target `>=0.2,<0.3`.

## [0.1.1] - 2026-07-27

Patch release: `windows-latest` CI is green for the first time (282 passed,
21 skipped — identical to Linux/macOS). Both fixes are CI/packaging-level;
no pipeline behaviour changes.

### Fixed

- Windows CI: `scripts/gen_schema_docs.py --check` failed on `windows-latest`
  (`schemas/pipeline.json`, `docs/reference/pipeline-schema.md` reported
  stale) while ubuntu/macOS passed. `PipelineSpec.state_dir` was declared
  `Path = Path(".arsenal")`, and on Windows pydantic cannot serialize the
  `WindowsPath` default (`PydanticJsonSchemaWarning: Default value .arsenal is
  not JSON serializable`), so it drops the `default` key from the generated
  schema — producing artifacts that differ from the committed (POSIX-generated)
  ones. `state_dir` is now `str = ".arsenal"` with a `Path` → `str` coercion
  validator, the same convention `ParquetSinkSpec.path` already used. YAML
  users are unaffected (YAML always yields a string), and Python callers
  passing a `Path` still work via the coercion. A regression test asserts no
  spec field default is a `pathlib` path, so this cannot come back without
  Windows in the loop.
- Windows CI: with the schema gate unblocked, `pytest` ran on `windows-latest`
  for the first time and the 18 testcontainers (Postgres/MySQL) tests ERRORed
  instead of skipping. The `docker_available()` guard added in `e4cefab` only
  checked daemon reachability, and the Windows runner *does* have a live
  daemon — in Windows-container mode, where starting a Linux image (and
  testcontainers' ryuk, with its `/var/run/docker.sock` bind mount) fails with
  `invalid volume specification`. The guard now also requires the daemon to
  report `OSType == "linux"`.

## [0.1.0] - 2026-07-19

First release. Covers milestones M1 (reliability core + collection) through
M4 (umbrella CLI + one-click recipes + release hardening).

### Added

#### Pugio (collection)

- **Sources**
  - REST source with 4 pagination modes: `offset`, `page`, `cursor` (dot-path
    extraction from a response envelope), and `link` (RFC 5988 `Link` header,
    GitHub/Shopify/GitLab style). Cursor and link modes persist the last
    completed cursor to the state store and resume from the frontier on
    restart, rather than replaying from the start.
  - File source: local CSV/JSONL/Excel to Arrow via glob pattern, one file =
    one unit (idempotent by construction). Excel support is an optional
    extra (`pugio[excel]`, via `fastexcel`).
  - Database source: SQLite via the standard-library `sqlite3` driver, and
    PostgreSQL/MySQL via a DuckDB `ATTACH` scanner — no hand-written driver
    or type-mapping code. Units are monotonic key-range chunks, so re-running
    against a grown table only adds new ranges (incremental sync falls out
    of the design; updates to existing rows are out of scope for P0).
  - Python custom-source escape hatch (`type: python`, `target: "pkg.module:ClassName"`)
    for APIs that don't fit the YAML shape. This is also the foundation the
    planned P1 `dlt` wrapper will build on.
- **Auth**: static token and OAuth2 client-credentials, with expiry-buffer
  preemptive refresh and 401-triggered refresh-then-retry-same-unit on
  expiry.
- **Rate limiting**: token-bucket limiter (`rate_limit.rps`) with adaptive
  slowdown on HTTP 429 (`Retry-After` respected).
- **Encoding**: non-UTF-8 REST/file responses (e.g. `euc-kr`) via an explicit
  `encoding` field.
- **Validation gate**: vectorized (`pyarrow.compute`, no row loops) rule
  checks — `not_null`, `unique`, `min`, `max` — with a per-pipeline policy
  of `block` / `quarantine` / `warn`.
- **Dead-letter queue**: quarantined units are isolated to
  `.arsenal/dlq/{pipeline}/{unit_id}.parquet` + a sibling `.json` with
  violation detail; `pugio dlq list` / `pugio dlq retry` inspect and requeue.
- **Sinks**: Parquet (deterministic per-unit filename + atomic `os.replace`),
  DuckDB (transactional delete+insert upsert on `merge_key`), and Postgres
  (`INSERT ... ON CONFLICT ... DO UPDATE`) — all covered by a shared
  idempotency/atomicity contract test suite.
- **Reliability core** (`arsenal-core`): deterministic unit IDs, a SQLite
  (WAL) state store tracking `pending/running/done/failed/quarantined` plus
  per-unit metrics, an error taxonomy (`RetryableError` / `AuthExpiredError`
  / `FatalError`), and a backoff retry wrapper that retries only retryable
  errors. This is what makes `kill -9` mid-run followed by re-run resume
  without duplication or gaps.
- Schema snapshot recording on each run (drift *detection and policy* are
  explicitly P1 — only the snapshot is recorded today).
- CLI: `pugio run <yaml>`, `pugio status <yaml>`, `pugio dlq list|retry`.

#### Gladius (transform)

- Declarative transform YAML: `map` (field-mapping table) plus `steps`
  (`filter` / `rename` / `cast` / `select` / `dedup` / `derive`), each step
  compiling to one CTE in a transparent, inspectable SQL chain.
- DuckDB execution engine, Parquet-in/Parquet-out via Arrow.
- CLI: `gladius compile <yaml>` (prints the generated SQL — no hidden
  behavior), `gladius run <yaml>`, and `gladius query "SELECT ..."` for
  instant ad-hoc SQL over collected Parquet (the "mini warehouse" moment).
- 1 GB / 13M-row single-node transform benchmark recorded in
  `benchmarks/RESULTS.md` (see [docs/08-limits.md](docs/08-limits.md) for how
  to read those numbers).

#### Arsenal (umbrella CLI)

- New package `packages/arsenal` (distribution `de-arsenal`, command
  `arsenal`) — a thin delegation layer over pugio/gladius, no new pipeline
  logic. `arsenal init <recipe>`, `arsenal run` (executes an `arsenal.yaml`
  manifest's pipelines then transforms, in-process, not via subprocess),
  `arsenal query`, plus `arsenal collect ...` / `arsenal transform ...`
  mounted sub-apps that delegate directly to the pugio/gladius CLIs.
- Three one-click recipes, each E2E-tested: `github-issues` (REST → parquet
  → cleaning transform), `csv-cleanup` (file-glob → parquet → dedup+cast
  transform), `api-to-postgres` (REST → validation gate → Postgres upsert
  sink).
- `examples/quickstart/`: an offline, no-account, no-Docker collect →
  transform → query walkthrough verified end-to-end by test.

#### Tooling / docs

- Generated YAML reference: `docs/reference/pipeline-schema.md` and
  `docs/reference/transform-schema.md` render directly from
  `PipelineSpec.model_json_schema()` / `TransformSpec.model_json_schema()`
  (`scripts/gen_schema_docs.py`), plus JSON Schema files for editor
  autocompletion. CI checks generated-doc freshness
  (`scripts/gen_schema_docs.py --check`).
- `docs/08-limits.md`: honest single-node processing envelope (with
  benchmark figures), an explicit "not supported" table, and a per-sink
  idempotency-guarantee table.
- Packaging: `uv build --all-packages` produces 4 wheels (`arsenal-core`,
  `pugio`, `gladius`, `de-arsenal`) with Apache-2.0 license metadata,
  Python 3.11/3.12 classifiers, and project URLs. Clean-venv wheel-install
  smoke test confirms `arsenal --help` / `pugio --help` / `gladius --help`
  all work with no editable/workspace leakage. CI matrix now includes
  `windows-latest`.

### Supported spec fields

Full field-by-field reference (types, defaults, descriptions) is generated
from the Pydantic models and kept current in:

- [`docs/reference/pipeline-schema.md`](docs/reference/pipeline-schema.md) —
  pipeline YAML (`PipelineSpec`): `name`, `state_dir`, `source`
  (discriminated union: `rest` | `file` | `database` | `python`), `sink`
  (discriminated union: `parquet` | `duckdb` | `postgres`), `validate`
  (aliased to `validation`).
- [`docs/reference/transform-schema.md`](docs/reference/transform-schema.md) —
  transform YAML (`TransformSpec`): `name`, `input`, `map`, `steps`
  (`filter` | `rename` | `cast` | `select` | `dedup` | `derive`), `output`.

Compact summary of the source/sink/auth building blocks:

| Block | Key fields |
|---|---|
| `source: rest` | `url`, `headers`, `pagination` (`mode: offset\|page\|cursor\|link`, `param`, `size_param`, `size`, `start_page`, `cursor_param`, `cursor_path`, `record_path`), `rate_limit.rps`, `encoding`, `auth`, `method` (`GET`/`POST`), `body` |
| `source: file` | `path` (glob), `format` (`auto`/`csv`/`jsonl`/`excel`), `encoding` |
| `source: database` | `dialect` (`postgres`/`mysql`/`sqlite`), `dsn_env`, `table`, `split` (`key`, `chunk`) |
| `source: python` | `target` (`"pkg.module:ClassName"`), `options` |
| `auth` | `type` (`static_token`/`oauth2_client_credentials`), `token_env`, `token_url`, `client_id_env`, `client_secret_env`, `expiry_buffer_s` |
| `validate` | `rules[]` (`field`, `not_null`, `unique`, `min`, `max`), `on_violation` (`block`/`quarantine`/`warn`) |
| `sink: parquet` | `path` |
| `sink: duckdb` | `path`, `table`, `merge_key[]` |
| `sink: postgres` | `dsn_env`, `table`, `merge_key[]` |

### Known limitations

Full detail: [docs/08-limits.md](docs/08-limits.md).

- **Postgres sink requires the optional `postgres` extra (`psycopg`).**
  Neither `de-arsenal` nor `pugio`'s base install pulls in `psycopg` —
  install with `uv tool install "de-arsenal[postgres]"` (or
  `pip install "pugio[postgres]"`) before running the `api-to-postgres`
  recipe. Without the extra, `build_sink` on a `sink: postgres` spec raises a
  clean `FatalError` ("postgres sink requires the 'postgres' extra: pip
  install pugio[postgres]") instead of a raw `ModuleNotFoundError`.
- **P0 scope boundary (by design, not a bug)**: no real-time streaming, no
  distributed execution, no schema-drift detection/policy (snapshot is
  recorded, comparison/alerting is not), no incremental transform
  (`incremental: by_unit/by_key`), no `dlt`/`dbt` interop, no S3/GCS sink.
  All of the above are scoped to P1/P2 in [docs/01-scope.md](docs/01-scope.md)
  and reaffirmed in
  [docs/plans/2026-07-12-draft-handoff.md](docs/plans/2026-07-12-draft-handoff.md)
  (ADR #6 build-vs-borrow, ADR #7 escape-hatch-from-P0).
- **`pugio dlq retry` does not support `cursor`/`link` pagination.** Cursor
  frontiers only move forward, so a quarantined past page's `unit_key`
  cannot be reproduced by a future run; `dlq retry` explicitly refuses this
  combination and preserves the DLQ file as evidence rather than silently
  orphaning it. `offset`/`page`/`file`/`database`/`python` sources retry
  normally.
- **DuckDB-scanner database source (postgres/mysql via `ATTACH`) can
  misclassify a bad-credential failure as retryable.** `duckdb.IOException`
  / `ConnectionException` / `TransactionException` are all currently
  classified as `RetryableError`, and DuckDB may surface a permanent auth
  failure as one of these — unlike the direct Postgres sink, which already
  separates sqlstate `28xxx` (auth) into `FatalError`. Net effect: wasted
  retries until backoff is exhausted, not incorrect data.
- **PyPI naming**: the `gladius` distribution name is owned by an unrelated
  project. v0.2.0 resolves this with the `de-gladius` distribution name while
  preserving the `gladius` import path and CLI.
