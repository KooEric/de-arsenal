# DE Arsenal

> **One laptop is a data platform.** One install, one command: ingest → validate → transform → query.

**English** | [한국어](README.ko.md)

[![PyPI](https://img.shields.io/pypi/v/de-arsenal?label=de-arsenal)](https://pypi.org/project/de-arsenal/)
[![Python](https://img.shields.io/pypi/pyversions/de-arsenal)](https://pypi.org/project/de-arsenal/)
[![CI](https://github.com/KooEric/de-arsenal/actions/workflows/ci.yml/badge.svg)](https://github.com/KooEric/de-arsenal/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Ingestion jobs that die at 3 AM, tokens that expire mid-run, a folder of CSVs nobody can query —
DE Arsenal handles them with one declarative command. No servers, no clusters, no DAG code.

```bash
uv tool install de-arsenal          # or: pip install de-arsenal
arsenal init csv-cleanup            # start from a recipe
arsenal run                         # ingest → validate → transform; interrupted? rerun to resume
arsenal query "SELECT * FROM './data/clean/*.parquet' LIMIT 10"
```

The **completeness** of a Databricks or Snowflake, in the **opposite shape**: where big platforms do a
hundred things at 80%, we do one thing at 100%. Idempotency, resume and validation are defaults, and the
zero-margin cost structure falls out of the architecture ([why](docs/07-cost-efficiency.md)).

## Status — v0.5.0

| | |
|---|---|
| **Stable** | Ingestion, transformation, the umbrella CLI and every P1 extension — dlt/dbt interop, schema-drift policies, cloud sinks, Python UDFs, observability, orchestration, backfill, data contracts |
| **Beta** (0.4) | **Hasta** — polling microbatch streaming and Postgres outbox CDC · **Ballista** — portable transforms on Spark/Kubernetes with Iceberg output |
| **Preview** (0.5) | **Aquila** — a file-backed catalog: datasets, schema history, cross-tool lineage, contracts, tags |
| **Experimental** | **Augur** — natural-language questions to SQL, with an eval harness |
| **Upgrading** | [0.4 → 0.5](docs/upgrade-0.5.md) (no state migration) · [0.3 → 0.4](docs/upgrade-0.4.md) · [CHANGELOG](CHANGELOG.md) |

## Install what you need

Every tool installs on its own; the umbrella pulls in only the batch core. Heavy runtimes
(Spark, Kubernetes, Iceberg, Kafka clients) are never part of the base install.

| You want | Install |
|---|---|
| Ingest, transform, query, recipes | `uv tool install de-arsenal` |
| …plus a Postgres sink | `uv tool install "de-arsenal[postgres]"` |
| …plus streaming & CDC (Hasta) | `uv tool install "de-arsenal[streaming]"` |
| …plus Spark-on-Kubernetes runs (Ballista) | `uv tool install "de-arsenal[distributed]"` |
| …plus the catalog (Aquila) | `uv tool install "de-arsenal[catalog]"` |
| …plus dbt or dlt projects | `"de-arsenal[dbt]"` · `"de-arsenal[dlt]"` |
| A single tool, nothing else | `pip install pugio` · `de-gladius` · `de-hasta` · `de-aquila` · … |

## Quickstart

**1. Ingest with Pugio.** Kill it mid-run (Ctrl-C) and rerun — it resumes. Rerun after it finishes — nothing happens.

```bash
export GITHUB_TOKEN=ghp_...
pugio run examples/github-issues.yaml
pugio status examples/github-issues.yaml --cost --max-age-seconds 86400
```

**2. Transform with Gladius.** Declarative steps compile to SQL you can read before it runs on DuckDB.

```bash
gladius compile examples/transform.yaml      # the exact SQL, no magic
gladius run examples/transform.yaml
gladius query "SELECT * FROM './data/issues_clean/*.parquet' LIMIT 10"
```

**3. Or let Arsenal run the whole project** from `arsenal.yaml`.

```bash
arsenal init --list          # github-issues · csv-cleanup · api-to-postgres
arsenal init csv-cleanup
arsenal run
arsenal status               # stream health, distributed runs, catalog — JSON, exit 1 on alerts
```

| Recipe | Pipeline |
|---|---|
| `github-issues` | REST API → clean table |
| `csv-cleanup` | folder of CSVs → dedup / cast |
| `api-to-postgres` | REST API → validation gate → Postgres upsert (needs `[postgres]`) |

**From source:** clone, run `uv sync`, and prefix the commands with `uv run`.

## Beyond batch

**Stream** a cursor API or a Postgres outbox, with checkpoints that survive crashes:

```bash
python examples/hasta/fake_orders_api.py &           # a local cursor API to play with
hasta run examples/hasta/orders-stream.yaml          # Ctrl-C commits the batch in flight, then stops
hasta checkpoint show examples/hasta/orders-stream.yaml
```

**Distribute** a Gladius transform on Spark/Kubernetes; the result is committed to Iceberg exactly once per run:

```bash
ballista plan big.yaml --profile spark.yaml --probe  # checks the live cluster, storage and catalog first
ballista run big.yaml --profile spark.yaml --input-version auto
```

**Catalog** everything the repo produces — no tool depends on it, and scans never write to state databases:

```bash
aquila scan
aquila lineage orders_daily --direction up --format mermaid
aquila history orders                                # schema versions with diffs
aquila tag orders pii=true && aquila search pii
```

## The armory

You only need to know `arsenal`. Underneath are independent tools — one per problem — loosely coupled through
an Arrow/Parquet hub, so there is no lock-in.

| Weapon | What it does | Package | Status |
|---|---|---|---|
| **Arsenal** | Umbrella CLI — `init` / `run` / `status` / `query`, three one-command recipes | `de-arsenal` | ✅ stable |
| **Pugio** | Ingestion & loading — REST, files, databases, dlt; resumable, idempotent sinks | `pugio` | ✅ stable |
| **Gladius** | Declarative transforms compiled to DuckDB SQL; incremental runs, UDFs, ad-hoc queries | `de-gladius` | ✅ stable |
| **Scutum** | Data contracts, DLQ policy, file locks with retry/backoff | `de-scutum` | ✅ stable |
| **Scorpio** | Freshness alerts, lineage, stream health and run summaries | `de-scorpio` | ✅ stable |
| **Spatha** | Orchestration — signal-aware DAG, priority, run windows, locks, command tasks | `spatha` | ✅ stable |
| **Onager** | Isolated backfills and small-file compaction, safe next to live streams | `de-onager` | ✅ stable |
| **Hasta** | Streaming & CDC — polling microbatches, Postgres outbox, fenced checkpoints | `de-hasta` | 🧪 beta |
| **Ballista** | Distributed transforms — Spark on Kubernetes, Iceberg commits, DuckDB↔Spark equivalence | `de-ballista` | 🧪 beta |
| **Aquila** | Catalog & governance — dataset registry, schema history, lineage, contracts, search | `de-aquila` | 🧪 preview |
| **Augur** | Natural language → SQL over the lake (schema RAG) with a failure-mode eval harness | `de-augur` | 🧪 experimental |
| **Pilum** | Reverse ETL — idempotent dispatch to webhooks, HTTP APIs and Slack | — | 📋 planned (0.6) |

The Databricks product line, mapped to these weapons: [docs/00-overview.md](docs/00-overview.md).

## Principles

Four promises, kept at the same time — we never trade one for another.

- **Light** — pick up one tool and use it. `pip install pugio` is a complete start.
- **Fast** — bulk, vectorized, parallel. Small doesn't mean slow.
- **Correct** — idempotency, resume and validation by default. Interruptions leave no duplicates and no gaps.
- **Easy for everyone** — YAML, occasionally SQL. Analysts use it directly; there is no DAG code to write.

What we guarantee — and, just as explicitly, what we don't: [docs/08-limits.md](docs/08-limits.md).

## Docs

Design documents are written in Korean; the YAML references and the changelog are language-neutral.

| Start here | |
|---|---|
| [docs/tools.md](docs/tools.md) | When to use each tool, with quick starts |
| [docs/reference/pipeline-schema.md](docs/reference/pipeline-schema.md) · [transform-schema.md](docs/reference/transform-schema.md) | Every YAML field, generated from the code |
| [docs/08-limits.md](docs/08-limits.md) | Honest limits — single-node envelope, per-sink and per-tool guarantees |
| [CHANGELOG.md](CHANGELOG.md) · [upgrade 0.5](docs/upgrade-0.5.md) · [upgrade 0.4](docs/upgrade-0.4.md) | What changed and how to upgrade |

| Design | |
|---|---|
| [00-overview](docs/00-overview.md) · [01-scope](docs/01-scope.md) · [roadmap](docs/roadmap.md) | Vision, positioning, scope, the original problem statements |
| [02-architecture](docs/02-architecture.md) · [03-tech-stack](docs/03-tech-stack.md) | Monorepo layout, shared core, state model, technology choices |
| [07-cost-efficiency](docs/07-cost-efficiency.md) · [09-oss-leverage](docs/09-oss-leverage.md) | Cost structure; what we borrow from open source and why |

| Building & operating | |
|---|---|
| [04-implementation-plan](docs/04-implementation-plan.md) · [plans/](docs/plans/) | Milestone work breakdown and detailed TDD plans |
| [05-testing-plan](docs/05-testing-plan.md) · [06-conventions](docs/06-conventions.md) | Test strategy; coding, commit and CI conventions |
| [10-augur-eval-protocol](docs/10-augur-eval-protocol.md) · [dogfooding](docs/dogfooding.md) | Evaluating Augur; a two-week dogfooding runbook |
| [release](docs/release.md) | Versioning, build and PyPI release procedure |
| [11-real-data-experiments](docs/11-real-data-experiments.md) · [experiments/](experiments/real-data/README.md) | Results on real APIs and data (GitHub, GitLab, airports CSV, 19.7M NYC taxi rows), bugs found, limits confirmed |

## License

[Apache-2.0](LICENSE)
