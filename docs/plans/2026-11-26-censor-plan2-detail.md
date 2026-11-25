# Censor Plan 2: Standard Tier — Detailed Plan

Outline and Task 0 findings: [2026-11-26-censor-plan2-standard.md](2026-11-26-censor-plan2-standard.md).
Spec: [2026-11-25-censor-design.md](2026-11-25-censor-design.md) §3, §6, §8, §9.
Style reference: [2026-11-25-censor-plan1-foundation.md](2026-11-25-censor-plan1-foundation.md).

**Goal:** `censor audit --tier standard` scores reliability, freshness and data health by reading state
and data only. Files, a DuckDB table and a Postgres table can be audited without a project.

**Architecture:** One frozen `AuditContext` describes the target (project or data), the clock and the
scan options. Check groups declare the target kinds they apply to; the runner skips the rest, so a
dimension with no applicable group is `na`. Data reading goes through one DuckDB connection per data
target (`censor/data.py`), guarded by `censor/limits.py`. State is read from a copy (Task 0).

## Global constraints

Same as Plan 1 and the outline: nothing is written outside `<base>/.arsenal/censor/`; no pipeline is
re-run; grades are deterministic (clock injected, fixed sample seed); JSON on stdout, people on stderr;
exit 0 / 1 / 2; DSNs by environment variable name only and never echoed; pyright strict, ruff 100
columns, coverage 80%; report schema only gains fields in `schema_version` 1.

Quick behaviour and the pinned maturity scores (70.21 / 87.29 / 96.46 / 100) must not move. Existing
tests are not edited to make them pass.

## File structure (new or changed)

```
packages/censor/src/censor/
  target.py        TargetKind, FilesTarget, DuckDBTarget, PostgresTarget, ScanOptions, AuditContext
  result.py        make_result (the one result helper; replaces _r/_result in four modules)
  runner.py        CheckGroup(kinds=...), run_checks skips groups whose kinds exclude ctx.kind
  limits.py        MAX_FILES, MAX_BYTES, MAX_ESTIMATED_ROWS, Scale, measure_*, exceeds, guidance
  state.py         read_pipeline_state: copy <name>.db(+ -wal) to .arsenal/censor/tmp, read the copy
  data.py          DataSource (connection + relation SQL), open_files/open_duckdb/open_postgres,
                   run_query (timeout by interrupt), profile() -> Profile
  derive.py        project spec -> data targets (parquet/duckdb sinks, transform output)
  checks/reliability.py, checks/freshness.py, checks/data_health.py
  checks/__init__.py   QUICK_CHECKS (unchanged order), STANDARD_CHECKS
  audit.py         AuditRequest, AuditOutcome(report, tool_errors), run_audit; audit_project stays
  model.py         AuditReport + notices, sample, since (additive)
  report.py        "Scale and sample" section
  cli.py           target and scan options, tier standard
packages/censor/tests/
  _data.py         parquet/csv/duckdb fixture writers, snapshot() hashing helper
  test_target_runner.py, test_result.py, test_state.py, test_reliability.py, test_freshness.py,
  test_data_profile.py, test_data_health.py, test_limits.py, test_derive.py,
  test_postgres_target.py, test_read_only.py, test_cli_standard.py
```

## Task 1: Generalise the target

- `target.py`
  - `TargetKind = Literal["project", "files", "duckdb", "postgres"]`.
  - `FilesTarget(pattern)`, `DuckDBTarget(path, table)`, `PostgresTarget(table, dsn_env)`;
    `DataTarget` is their union. Each has `label` (what the report shows; never a DSN).
  - `ScanOptions(sample_percent=None, since=None, full=False, memory_limit="4GB", timeout_seconds=120.0)`.
  - `AuditContext(kind, root, now, project, data, key, time_column, max_age_hours, scan)`.
    `root` is the base directory: the project root, or the directory whose `.arsenal/censor/` gets
    the history for a data target.
- `runner.py`: `CheckGroup` is generic over the context and gains
  `kinds: frozenset[TargetKind] = ALL_KINDS`. `run_checks(ctx, groups)` needs only `ctx.kind` and
  `ctx.root` (a `Protocol`), so the existing `ProjectContext` tests keep working; `ProjectContext`
  gets `kind = "project"`.
- `result.py`: `make_result(check_id, dimension, weight, status, subject, evidence, fix="", *,
  blocker=False)`; clears `fix` on pass. The four modules call it through a two-line local wrapper
  only where they add an id prefix.
- `audit.py`: `run_audit(AuditRequest) -> AuditOutcome`; fills `tier` and `target_kind` from the
  request. `audit_project(root, now=)` stays as the Quick wrapper.
- Tests first: a group whose kinds exclude the target is not run and its dimension scores `None`;
  a crashing group still becomes one `error`; `make_result` clears the fix on pass; a files-kind
  context with Quick groups yields no checks. Then run the whole existing suite unchanged.

## Task 2: Reliability and freshness (project)

- `state.py`: `read_pipeline_state(root, spec_file, scratch) -> PipelineState | None`
  (`None` = never run). Name and `state_dir` come from the raw YAML (default `.arsenal`), so an
  unset environment variable does not block the read. Relative `state_dir` resolves against the
  project root. Copies `<name>.db` and `<name>.db-wal` into `scratch` (`.arsenal/censor/tmp/<random>`),
  opens the copy with `StateStore`, reads `counts`, `pipeline_metrics`, `lease("pipeline:<name>")`,
  removes the copy. `split_rejected_rows(state_dir, name)` reads the DLQ in place (it only globs and
  reads JSON). `sqlite3.Error` and `FatalError` while reading -> `PipelineState(busy=True)`.
- `checks/reliability.py` (per pipeline `<spec>`; never-run -> every check `na`):

  | id | weight | pass | warn | fail |
  |---|---|---|---|---|
  | `reliability.failed_units:<spec>` | 3 | no failed or quarantined unit | – | any |
  | `reliability.rejected_rows:<spec>` | 2 | none | ratio <= `DLQ_FAIL_RATIO` (0.05) | above it |
  | `reliability.output_kept:<spec>` | 2 | failures exist and local output still has files | – | output gone |
  | `reliability.active_run:<spec>` | 1 | – | unexpired lease, or state busy | – |

  `output_kept` is `na` when nothing failed or the sink is not a local path. `active_run` is emitted
  only when it warns. Project-level `reliability.dataset_publication` (weight 2): `arsenal dataset`
  failed but `published` is still there -> pass; failed and no `published` -> fail; otherwise `na`.
  The rejected ratio is rejected rows / (rows of done units + rejected rows).
- `checks/freshness.py`:

  | id | weight | rule |
  |---|---|---|
  | `freshness.last_success:<spec>` | 2 | pass when a unit completed (evidence: time, age in hours); `na` never-run |
  | `freshness.dataset_state` | 3 | published -> pass, stale or failed -> fail, never_run -> `na` |
  | `freshness.max_age_set` | 1 | `max_age_hours` in the dataset rules -> pass, else warn; `na` without a dataset |

  `status_report(root/.arsenal/dataset.duckdb)` supplies the fields; staleness is recomputed with
  the injected `now` because `status_report` reads the wall clock.
- Tests: build state with `StateStore` directly (done, failed, quarantined units; DLQ JSON with
  `mode: split`); never-run -> all `na`; an unexpired lease -> warn; a v1-schema DB is read without
  being migrated; source directory snapshot equal before and after.

## Task 3: Data health (files, DuckDB)

- `data.py`
  - `DataSource(con, relation, label, sampled, columns)`; `relation` is a SQL fragment.
  - `open_files(target, scan, scratch)`: reader by suffix (`read_parquet`, `read_csv_auto`,
    `read_json_auto`); mixed suffixes -> `FatalError`. `open_duckdb`: in-memory connection,
    `ATTACH '<path>' AS target (READ_ONLY)`. Both `SET memory_limit`, `SET temp_directory`.
  - `profile(source, key, time_column, timeout) -> Profile(rows, duplicate_keys, null_keys,
    null_ratios, time_min, time_max)`. One aggregate scan plus one `GROUP BY key HAVING count(*) > 1`.
    Identifiers are quoted with `gladius.compile.ident.quote_ident`; a column that does not exist
    is a `FatalError` naming the column.
- `checks/data_health.py` (per data target `<label>`):

  | id | weight | pass | warn | fail |
  |---|---|---|---|---|
  | `data_health.rows:<label>` | 2 | rows > 0 | – | 0 rows |
  | `data_health.key_unique:<label>` | 3 | no duplicate key | sampled and none found | duplicates |
  | `data_health.key_not_null:<label>` | 2 | no NULL in the key | – | any |
  | `data_health.null_ratio:<label>` | 2 | every column below warn | a column >= `NULL_WARN_RATIO` (0.5) | a column at `NULL_FAIL_RATIO` (1.0) |
  | `data_health.period:<label>` | 1 | min and max present | max later than now + 1 day | all NULL |

  No `--key` -> the two key checks are `na` with "pass --key". No `--time-column` -> `period` is `na`.
- `freshness.data_age:<label>` (weight 2, data targets): newest `--time-column` value (files without
  one: newest file mtime) against `--max-age-hours`; `na` when no limit was given.
- `derive.py`: project -> data targets. Parquet sink with a local path -> `FilesTarget(path/**/*.parquet)`,
  key from `validate` rules marked `unique`; DuckDB sink -> `DuckDBTarget(path, table)` with `merge_key`;
  transform `output` -> `FilesTarget`, key from a `dedup` step. Remote paths and other sinks -> one
  `data_health.target:<spec>` `na` with the reason. Output that does not exist yet -> `na`.
- Tests: parquet fixtures with a duplicate key, NULLs and a period; CSV; a DuckDB file with `published`;
  missing key column -> exit 2 path; derived targets from the maturity fixtures.

## Task 4: Scale guard

- `limits.py`: constants from spec §6; `Scale(files, bytes, estimated_rows)`;
  `measure_files(pattern)` (glob + `stat`), `measure_duckdb(con, table)` (`duckdb_tables().estimated_size`
  and the file size), `exceeds(scale) -> bool`; `guidance(label, scale, time_column)` returns the
  spec §6 text.
- Over the limit and none of `--sample`, `--since`, `--full` -> one `data_health.skipped:<label>` `na`,
  the guidance in `report.notices` and on stderr. Other dimensions are unaffected.
- `--sample P%`: with a key, `WHERE hash(key) % 10000 < P*100` (all rows of a sampled key land in
  the sample, so a duplicate found there is real); without a key, `USING SAMPLE P PERCENT
  (bernoulli, 42)` with `threads=1` for a repeatable result. `--since D` needs `--time-column`.
- Timeout: a timer calls `con.interrupt()`; the checks of that target become `na` with `timeout 120s`.
- `AuditReport.sample` (e.g. `"1% by key hash"`) and `since` head the report.
- Tests: limits at the boundary by monkeypatching the constants; guidance text; sampled duplicate ->
  fail, sampled clean -> warn; same sample twice -> same result; timeout -> `na`;
  temp directory is `.arsenal/censor/tmp/`.

## Task 5: Postgres target

- `open_postgres(target, scan)`: DSN from `os.environ[dsn_env]` (unset -> `FatalError` naming the
  variable only); `statement_timeout` appended to the DSN as `options=-c statement_timeout=<ms>`
  (URL and keyword forms); `ATTACH ... (TYPE POSTGRES, READ_ONLY)`.
- Table existence by `to_regclass`; scale by `reltuples` / `pg_total_relation_size` through
  `postgres_query`; `reltuples = -1` falls back to bytes.
- `--sample`: `postgres_query('src', 'SELECT * FROM <t> TABLESAMPLE SYSTEM (P) REPEATABLE (42)')`.
- Connection or authentication failure, missing table: `data_health.unreadable:<label>` `na`, the
  outcome carries a tool error -> exit 2 after the JSON is printed. Every message passes through a
  DSN redactor. `canceling statement due to statement timeout` -> `na` `timeout`.
- Tests (testcontainers, `importorskip`): duplicate key -> fail; estimated rows over a patched limit
  -> skipped with guidance; `--sample` works and repeats; timeout -> `na`; wrong password -> exit 2 and
  no DSN in stdout or stderr; row count equal before and after.

## Task 6: CLI, report, docs

- `censor audit [DIR] [--tier] [--files G | --duckdb P --table T | --table T --postgres-dsn-env E]
  [--key C]... [--time-column C] [--max-age-hours H] [--sample P] [--since D] [--full]
  [--memory-limit S] [--timeout N]`. A data target with `--tier quick` is exit 2 (nothing to check).
- Default tier: **ask the user before changing it** (behaviour change for 0.7.0).
- Markdown: header lines for sample and since; a "Scale and sample" section listing the notices.
- `arsenal eval --help` forwards to Censor's help (`help_option_names: []`).
- Docs: `packages/censor/README.md`, `docs/tools.md` §10, the Censor rows in `README.md` and
  `README.ko.md`, CHANGELOG, regenerated `schemas/censor-report.json`.
- Carried over from Plan 1: remove the Parquet when a later save step fails; `censor report` on a
  missing directory exits 2; an unreadable `arsenal.yaml` exits 2; `_DAG_TRIGGER` ignores comment
  lines; unique temp file names; a dedicated `MissingEnvError` in `arsenal_core`. Not done here:
  requiring the secret hook to be registered in `settings.json` — it would lower the pinned
  production fixture score, which this plan must not move.

## Task 7: Read-only proof and the full gate

- `test_read_only.py`: snapshot (path -> sha256, size, mtime) of the whole target tree, excluding
  `.arsenal/censor/`, equal before and after a Standard audit — project with state DB (including WAL
  sidecars and a v1-schema DB), files, DuckDB file; Postgres row count and `pg_stat_user_tables`
  write counters equal.
- e2e: `arsenal init csv-cleanup` -> `arsenal run` -> `censor audit . --tier standard` scores the
  three Standard dimensions.
- Gate: `uv run ruff format --check . && uv run ruff check . && uv run pyright &&
  uv run python scripts/gen_schema_docs.py --check && uv run pytest` with Docker running.
