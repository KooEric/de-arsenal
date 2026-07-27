# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project has not yet reached a public API stability commitment (see
[Known limitations](#known-limitations)); spec-field additions are
backward-compatible by policy (`arsenal-core` pinned `>=0.1,<0.2` across
`pugio`/`gladius`/`de-arsenal`).

## [Unreleased]

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
- **PyPI name collision**: `de-arsenal` and `pugio` are unclaimed;
  `gladius` is already registered by an unrelated project. No rename has
  been made in this repo — see
  [docs/reference/packaging.md](docs/reference/packaging.md) for the
  prefix-strategy options. This is a publish-time decision for the
  repository owner, not resolved by this release.
