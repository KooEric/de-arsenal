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
It is a single-node tool. What has been measured on real data
([docs/11-real-data-experiments.md](docs/11-real-data-experiments.md), 4 vCPU):

- 9,554 GitLab issues over 96 pages with a `kill -9` mid-run: the loaded count equals
  the API's total, with no duplicate ids.
- 9.55M NYC taxi rows loaded in 4.0 s; after a crash and resume the row count and the
  amount total match the source.
- A stream of 24,000 events killed six times: none missing, none duplicated.

There is no comparison with other tools yet.

## Tools

`arsenal` runs a whole project. Each tool underneath also installs and runs on its own,
and they exchange data only as Parquet files.

| Tool | Does | Package | Status |
|---|---|---|---|
| **Arsenal** | Project CLI: `init`, `run`, `status`, `query`; three starter recipes | `de-arsenal` | stable |
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
