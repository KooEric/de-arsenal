# DE Arsenal

Command-line tools for data pipelines that run on one machine: ingest from APIs, files
and databases into Parquet, transform with DuckDB, and query the result. Pipelines are
YAML files. There is no server to run.

**English** | [한국어](README.ko.md)

[![PyPI](https://img.shields.io/pypi/v/de-arsenal?label=de-arsenal)](https://pypi.org/project/de-arsenal/)
[![Python](https://img.shields.io/pypi/pyversions/de-arsenal)](https://pypi.org/project/de-arsenal/)
[![CI](https://github.com/KooEric/de-arsenal/actions/workflows/ci.yml/badge.svg)](https://github.com/KooEric/de-arsenal/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

```bash
uv tool install de-arsenal          # or: pip install de-arsenal
arsenal init csv-cleanup            # scaffolds a project with a sample CSV in input/
arsenal run                         # ingest, then transform
arsenal query "SELECT * FROM './data/clean/*.parquet' LIMIT 10"
```

These four commands run as written; CI runs the same `init` and `run` on every change
(`tests/e2e/test_recipes.py`). To use your own data, replace `input/sample.csv`.

## What it does

- **Resumes.** Each page, file or batch is recorded when its write finishes. Stop a
  run with Ctrl-C or `kill -9` and run it again: finished units are skipped.
- **Does not duplicate.** Output file names and upsert keys are derived from the input
  unit, so a repeated unit overwrites itself.
- **Shows its SQL.** `gladius compile` prints the DuckDB SQL a transform will run.
- **Fails loudly on ambiguous input.** A path that matches no files, a paging mode
  that cannot work with its parameter, and columns whose type differs between files
  are errors or warnings, not silent successes.

What is and is not guaranteed, per sink and per tool: [docs/08-limits.md](docs/08-limits.md).
It is a single-node tool. What was run against real APIs and data, what broke and what is
still unproven is in [Tested on real data](#tested-on-real-data). There is no comparison with
other tools yet.

## Tools

`arsenal` runs a whole project. Each tool underneath also installs and runs on its own,
and they exchange data only as Parquet files.

| Tool | Does | Package | Status |
|---|---|---|---|
| **Arsenal** | Project CLI: `init`, `check`, `run`, `status`, `query`; three starter recipes | `de-arsenal` | stable |
| **Pugio** | Ingestion: REST, CSV/JSONL/Excel, databases, dlt sources → Parquet, DuckDB, Postgres, S3/GCS, Redshift and Iceberg (both experimental) | `pugio` | stable |
| **Gladius** | YAML transforms compiled to DuckDB SQL; incremental runs, Python UDFs, ad-hoc queries | `de-gladius` | stable |
| **Onager** | Small-file compaction and isolated backfills for local Parquet | `de-onager` | beta |
| **Scutum** | Library: Arrow data contracts and a file lock (used by Pugio) | `de-scutum` | beta |
| **Scorpio** | Library: freshness checks and stream health (used by `status` commands) | `de-scorpio` | beta |
| **Spatha** | Library: pick runnable tasks from dependencies, signals, priority and time windows. No scheduler daemon | `spatha` | beta |
| **Hasta** | Streaming: polling microbatches from a cursor API, Postgres outbox CDC | `de-hasta` | beta |
| **Ballista** | Runs a Gladius transform on Spark/Kubernetes and commits to Iceberg | `de-ballista` | beta |
| **Aquila** | Catalog in one SQLite file: datasets, schema history, lineage, contracts, tags | `de-aquila` | preview |
| **Augur** | Natural-language questions to SQL, with an eval harness | `de-augur` | experimental |
| **Pilum** | Reverse ETL: send changed records to HTTP endpoints and Slack, once per change | `de-pilum` | experimental |

**stable**: YAML fields are only added within a minor series, and state files migrate
forward. **beta**: works and is tested, but has little use outside this repo.
**preview / experimental**: formats and commands may change in any release.
The project is pre-1.0 and has no long-term API commitment; see the
[CHANGELOG](CHANGELOG.md).

## Install

| You want | Install |
|---|---|
| Ingest, transform, query, recipes | `uv tool install de-arsenal` |
| …plus a Postgres sink | `uv tool install "de-arsenal[postgres]"` |
| …plus a Redshift sink | `uv tool install "de-arsenal[redshift]"` |
| …plus an Iceberg sink (readable from Trino) | `uv tool install "de-arsenal[iceberg]"` |
| …plus streaming and CDC (Hasta) | `uv tool install "de-arsenal[streaming]"` |
| …plus Spark-on-Kubernetes runs (Ballista) | `uv tool install "de-arsenal[distributed]"` |
| …plus the catalog (Aquila) | `uv tool install "de-arsenal[catalog]"` |
| …plus reverse ETL (Pilum) | `uv tool install "de-arsenal[dispatch]"` |
| …plus dbt or dlt projects | `"de-arsenal[dbt]"` · `"de-arsenal[dlt]"` |
| A single tool | `pip install pugio` · `de-gladius` · `de-hasta` · `de-aquila` · `de-pilum` · … |

The base install does not pull in Spark, Kubernetes, Iceberg or Postgres drivers
(`packages/arsenal/tests/test_base_install.py` checks this).
From source: clone, `uv sync`, and prefix commands with `uv run`.

## Examples

Every block below runs from a clone of this repository.

**Ingest and transform, offline** ([examples/quickstart](examples/quickstart)):

```bash
cd examples/quickstart
pugio run collect.yaml                 # orders.csv → ./data/orders
gladius compile transform.yaml         # print the SQL
gladius run transform.yaml             # → ./data/orders_clean
gladius query "SELECT * FROM './data/orders_clean/*.parquet'"
```

**Ingest from a REST API** (needs a GitHub token):

```bash
export GITHUB_TOKEN=ghp_...
pugio run examples/github-issues.yaml
pugio status examples/github-issues.yaml --cost --max-age-seconds 86400
```

**Stream from a cursor API** (`[streaming]`):

```bash
python examples/hasta/fake_orders_api.py &
hasta run examples/hasta/orders-stream.yaml      # Ctrl-C commits the batch in flight
hasta checkpoint show examples/hasta/orders-stream.yaml
```

**Send changed records to an endpoint** (`[dispatch]`):

```bash
python examples/pilum/fake_alerts_api.py &
export ALERTS_URL=http://127.0.0.1:8766/alerts
pilum plan examples/pilum/alerts.yaml            # what would be sent; sends nothing
pilum run examples/pilum/alerts.yaml             # sends 2 records
pilum run examples/pilum/alerts.yaml             # sends nothing: nothing changed
```

**Catalog a project** (`[catalog]`), from any project directory that has run:

```bash
aquila scan
aquila search orders
```

Ballista needs a Kubernetes cluster, object storage and an Iceberg catalog; its setup is
in [packages/ballista/README.md](packages/ballista/README.md).

To run a project on a schedule (cron, GitHub Actions, containers), start from
[examples/deploy](examples/deploy).

Recipes for `arsenal init`: `csv-cleanup` (folder of CSVs → dedup and cast),
`github-issues` (REST API → clean table), `api-to-postgres` (REST API → validation →
Postgres upsert, needs `[postgres]`).

## Tested on real data

The test suite passing was not enough: two releases were needed for bugs that only real
data showed. This section is the record of what was run against real APIs and files,
what broke, and what is still unproven. Environment for the experiments: Linux, 4 vCPU,
15 GB RAM. Every number is from an actual run; specs to repeat them are in
[experiments/real-data/](experiments/real-data/README.md), the full report in
[docs/11-real-data-experiments.md](docs/11-real-data-experiments.md) (Korean).

| | |
|---|---|
| Crash-injected runs that ended with a duplicate or a missing row | **0** of 4 experiments (1, 2, 4, 5) |
| Silent data-loss bugs found by real data, all fixed | **5** (0.5.3: 2, 0.6.0: 3) |
| Largest real load | 9.55M rows in 4.0 s; 19.7M rows transformed in 5.96 s |
| Automated tests | 791, run in CI on Linux, macOS and Windows with Python 3.11 and 3.12 |

### Experiments

**1. GitHub API, killed mid-run.** 169 commits, 5 per page, `kill -9` during the fetch.

```text
$ pugio status gh-commits.yaml        # right after kill -9
done: 8
running: 1
$ pugio run gh-commits.yaml           # immediately
error: pipeline 'gh-commits' is busy (lease held by pugio-6b4b… until 02:17:16)
$ pugio run gh-commits.yaml           # after the lease expired
done: fetched=26 written=26 skipped=8 quarantined=0
$ pugio run gh-commits.yaml           # after completion
done: fetched=1 written=0 skipped=34 quarantined=0      # 0.70s
```

The dead runner's lease blocked a second writer, the resume skipped the 8 finished
pages, and the result was 169 rows with 169 distinct commit ids.

**2. GitLab API, 9,554 issues over `Link`-header paging.** Killed after 8 seconds.

```text
$ pugio status gl-issues.yaml         # right after kill -9
done: 6
running: 1
$ pugio run gl-issues.yaml            # resume
done: fetched=90 written=90 skipped=0 quarantined=0
$ count(*), count(DISTINCT id)  /  API header x-total
9554, 9554  /  9554
$ pugio run gl-issues.yaml            # after completion
done: fetched=0 written=0 skipped=0 quarantined=0       # 0.44s
```

Link paging cannot know the next URL in advance, so the last cursor is stored with the
state. The resume continued from it without re-requesting the 6 finished pages.
96 pages, 39 MB, limited to 5 requests per second.

**3. A messy public CSV through the validation gate.** World airports, 86,343 rows by
13 columns: 87.7% of ICAO codes empty, names in several scripts, coordinates as a
`"lat, lon"` string. Split into 7 files by continent, with 3 elevations in the EU file
corrupted to `999999`.

| Step | Result |
|---|---|
| First run | Only the EU file was quarantined, reason `max:elevation_ft=3`. The other 6 files loaded: 73,701 rows in 0.50 s |
| After fixing the source | The 6 good files were skipped, only EU was reprocessed. 86,343 rows, equal to the source, no duplicate `ident` |
| Transform | filter, derive (parse coordinates, feet to metres), dedup and select compiled to one CTE query: 0.29 s, 4,572 airports |
| Sanity query | Highest airport: Daocheng Yading (DCY), 4,411 m, which matches the real one |

**4. A stream killed six times.** A local cursor API (20,000 events, then 250 per
second), `kill -9` every 0.8 to 2.5 seconds, six times, then a clean stop.

```text
$ hasta status orders.yaml            # right after a crash
"status": "stalled", "reasons": ["a runner holds an expired lease (it likely died); it will be taken over"], "fence": 6
$ count(*), count(DISTINCT id), min(id), max(id)
rows=24000 distinct=24000 min=1 max=24000 gaps=0 dups=0
```

**5. NYC taxi trips, 19.7M rows across three schema generations** (2019, 2022, 2024).

| What | Result |
|---|---|
| Schema change between runs (2022-01 then 2024-01) | Blocked by `schema_drift: block`, naming one renamed column and six type changes |
| Bulk load with a crash (2024 Q1, 9.55M rows) | 4.0 s, about 2.4M rows per second. Killed after 5 of 10 units: no half-written file; after the resume the row count (9,554,778) and the `total_amount` sum (256,692,913.53) equal the source |
| Transform over all five files | 19.7M rows in, 19.2M out, 5.96 s. `airport_fee` and `Airport_fee` were merged |

**6. Benchmarks on other hardware** (synthetic data, warm cache, no network: an upper
bound for the engine, not an expected production rate).

| Workload | M3 Pro, 12 cores | Linux, 4 vCPU |
|---|---:|---:|
| Transform 1 GB, 13M rows of Parquet | 0.105 s | 0.376 s |
| Stream 1M events | 2.99 s | 3.26 s |
| Re-run a finished stream | 0.9 ms | 0.8 ms |

**7. Natural language to SQL (Augur).** 34 of 34 evaluation cases correct with
`gemini-3.1-flash-lite`. Read as "the evaluation set is too easy" (3 small tables), not
as "the model is perfect".

### Bugs that only real data found

Every one of these ended with exit code 0 and a success message.

| Found | What happened | Evidence | Fixed in |
|---|---|---|---|
| The `github-issues` recipe fetched one page | `mode: offset` sent row offsets to a page-numbered parameter, so page 0 was followed by page 5. An e2e test had mocked `page=0` and pinned the bug as correct | 6 of 21 issues collected | 0.5.3: 21 of 21. That spec is now a validation error |
| `NA` in a CSV became NULL depending on how files were split | pyarrow treats `NA` as a null token; a file whose column is all `NA` inferred the type `null` | 39,842 North American rows (46%) lost their `continent` | 0.5.3: only an empty cell is null |
| The README's first four commands failed | The `csv-cleanup` recipe shipped no input, the ingest step reported success with 0 rows, and the transform died on a raw DuckDB error | exit 1 on a fresh install | 0.6.0: sample data ships; an empty input path is an error |
| Leading zeros vanished from CSV columns | A zip code column was inferred as an integer | `01234` stored as `1234` | 0.6.0: such columns stay strings; `column_types` pins a type |
| A plain CSV could not be loaded into Postgres | The sink refused dates, naive timestamps, small integers and all-empty columns | `unsupported arrow type` on a real Postgres 16 | 0.6.0: all four load |

Two limits were confirmed and addressed in 0.6.0:

- **Validation quarantined whole units.** With 1.2% bad rows spread over every chunk of
  the NYC data, all 10 units were quarantined and 0 of 9.55M rows were loaded.
  `on_violation: split` now loads the rows that pass and sets aside only the failing ones.
- **Type differences between files of one run went unnoticed.** Pugio now warns and names
  the files, and fails the run under `schema_drift: block`.

### What these runs do and do not show

- **Shown:** after a crash at any point tried, a rerun produced the same rows as the
  source with no duplicates, on page-numbered and link REST paging, a bulk load and a
  stream. A finished run is a sub-second no-op.
- **Shown:** a coverage figure near 89% did not catch bugs that sit between the spec and
  what real APIs and files mean. The test suite now runs the recipes users receive and
  the README's first commands.
- **Not shown:** throughput against real network sources. The largest API run is 9,554
  rows; it proves correctness, not speed.
- **Not shown:** behaviour over time. Every run above is a single execution; nothing has
  run on a schedule for weeks.

### How each sink was verified

| Sink | Verified against | Not verified |
|---|---|---|
| Parquet (local) | Real files in every experiment above | |
| DuckDB | Real DuckDB files in the test suite | |
| Postgres | A real Postgres 16: idempotency, upsert, type round-trips | Managed services (RDS, Aurora) |
| Redshift (experimental) | `insert` path executed on Postgres, which accepts the same SQL; `copy` path through the statements it issues | **Any real Redshift cluster** |
| Iceberg (experimental) | Real Iceberg tables with a SQLite catalog and a local warehouse | REST and Glue catalogs, S3, **reading back from Trino** |
| `pugio ddl` for Trino | The printed statement only | **Running it in Trino** |
| Pilum (experimental) | A local endpoint over real HTTP; a crash between response and ledger write is simulated in tests | Real third-party APIs, Slack |
| Ballista | A kind cluster with 3 rows in CI: correctness only | A real cluster, any performance baseline |

## Documentation

| | |
|---|---|
| [docs/tools.md](docs/tools.md) | When to use each tool, with a short start for each |
| [docs/reference/](docs/reference/) | Every YAML field, generated from the code |
| [docs/08-limits.md](docs/08-limits.md) | Guarantees and non-guarantees |
| [docs/11-real-data-experiments.md](docs/11-real-data-experiments.md) · [experiments/](experiments/real-data/README.md) | Results on real APIs and data (GitHub, GitLab, airports CSV, NYC taxi), bugs found, limits confirmed |
| [CHANGELOG.md](CHANGELOG.md) · [upgrade to 0.6](docs/upgrade-0.6.md) | What changed and how to upgrade |
| [docs/README.md](docs/README.md) | Design documents, decision records and release procedure |

The reference and changelog are in English. The guides and design documents are in Korean.

## License

[Apache-2.0](LICENSE)
