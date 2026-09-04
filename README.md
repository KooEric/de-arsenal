# DE Arsenal

> **A data platform with no signup and no metering.** Runs on the laptop you already have, driven by the AI you already use.

**English** | [한국어](README.ko.md)

Video, marketing and design are full of free tools. Data has none. Snowflake and Databricks bill by credits and DBUs, and the free tier turns into an invoice once data accumulates. That is not stinginess, it is architecture: they process your data on their servers. DE Arsenal is built the other way round. No server, no account, no meter. Compute is your laptop; the driver is the AI you already pay for (Claude Code, Cursor, or a local model). The full direction is in [docs/10-direction.md](docs/10-direction.md) (Korean).

```bash
uv tool install de-arsenal                    # or: pip install de-arsenal — no signup
arsenal init csv-cleanup                      # start from a recipe (github-issues · csv-cleanup · api-to-postgres)
arsenal run                                   # ingest → validate → transform; interrupted? rerun to resume
arsenal query "SELECT * FROM './data/clean/*.parquet' LIMIT 10"
```

> **Status: v0.3.0, live on PyPI.** All 8 packages are published. Ingestion, transformation, the umbrella CLI, and the P1 extensions (dlt/dbt interop, schema drift policies, cloud sinks, Python UDFs, observability, orchestration, backfill, data contracts) work today. The transform package is distributed as `de-gladius`; its Python import and CLI remain `gladius`. **Next: the AI agent layer (Augur)** — L1/L2 roadmap in [docs/10-direction.md](docs/10-direction.md).

## What it costs

| | Snowflake | Databricks | BigQuery | **DE Arsenal** |
|---|---|---|---|---|
| Billing unit | Credits (warehouse uptime) | DBUs + cloud infra | Bytes scanned + storage | **None** |
| Free scope | Trial credits, expire | Trial period, limited community edition | 1 TB queries / 10 GB storage per month | **Everything, forever** |
| Account | Required | Required | Required | **Not needed** |
| Where the data lives | Their servers | Their servers (or your cloud + a management margin) | Their servers | **Your laptop, open Parquet** |
| Cost to leave | Egress + migration | Egress + migration | Egress | **0 — the files are already yours** |
| Ceiling | None (if you pay) | None (if you pay) | None (if you pay) | **Single-node envelope — [docs/08](docs/08-limits.md)** |

The last row is the honest boundary. Scale beyond one machine, cloud storage fees and LLM tokens are yours, and we add no margin on any of them. The core stays free forever; the exact scope of that promise is in [docs/08-limits.md, "The free boundary"](docs/08-limits.md#무료의-경계).

## Who it is for

Not companies replacing Databricks, but the people Databricks never targeted: solo analysts, early startups, students and researchers, marketers who want to touch their own data, side projects. Canva did not beat Photoshop; it took the people who were never going to buy Photoshop. Same seat.

## The armory (internal architecture)

Users only need to know `arsenal`. Underneath are independent tools, one per problem domain — each installable and usable on its own (no lock-in), loosely coupled through an Arrow/Parquet hub.

| Weapon | Domain | Priority | Status |
|---|---|---|---|
| **Arsenal** (umbrella CLI) | Single entry point — init/run/query, 3 one-command recipes | P0 (M4) | ✅ v0.3.0 |
| **Pugio** | Ingestion & loading (ETL engine) | P0 + P1 | ✅ v0.3.0 |
| **Gladius** | Transformation & querying (core processing) | P0 + P1 | ✅ v0.3.0 |
| **Spatha** | Orchestration (dependencies, scheduling) | P0 (idempotent core) / P1 | ✅ signal DAG · priority · window · lock |
| **Scutum** | Data quality, validation, protection | P0 (idempotent guard) / P1 | ✅ contract · DLQ policy · lock retry/backoff |
| **Scorpio** | Observability (monitoring, lineage) | P1 | ✅ lineage · freshness alert · cost summary |
| **Onager** | Backfill & large-scale reprocessing | P1 | ✅ isolated backfill · small-file compaction |
| **Hasta** | Streaming & CDC | P2 | 📋 planned |
| **Pilum** | Dispatch & reverse ETL | P2 | 📋 planned |
| **Ballista** | Large-scale distributed processing | P2 | 📋 planned |
| **Aquila** | Catalog & governance (the legion standard — reference point for assets) | P2 | 💡 proposed |

> We aim to be the low-cost, high-efficiency mini version of the Databricks product line — the mapping is in [docs/00-overview.md](docs/00-overview.md), the cost structure in [docs/07-cost-efficiency.md](docs/07-cost-efficiency.md).

## Product philosophy

Four promises, satisfied simultaneously. We never sacrifice one for another.

- **Light** — pick up a single tool and use it immediately. Start with one `pip install pugio`.
- **Fast** — bulk, vectorized, parallel. Small doesn't mean slow.
- **Correct** — idempotency, resume, and validation are defaults. Interruptions cause no duplicates and no gaps.
- **Easy for everyone** — declarative (YAML, occasionally SQL). Analysts use it directly. No DAG code.

## Quickstart

```bash
uv tool install de-arsenal        # postgres recipe: uv tool install "de-arsenal[postgres]"

# 1) Ingest (pugio) — kill it mid-run (Ctrl-C) and rerun to resume; rerun after completion is a no-op
export GITHUB_TOKEN=ghp_...
pugio run examples/github-issues.yaml
pugio status examples/github-issues.yaml                             # unit states (done/pending/failed/quarantined)
pugio status examples/github-issues.yaml --cost --max-age-seconds 86400

# 2) Transform (gladius) — declarative map/steps compile to SQL, executed on DuckDB
gladius compile examples/transform.yaml                              # inspect the exact SQL that will run (no magic)
gladius run examples/transform.yaml
gladius query "SELECT * FROM './data/issues_clean/*.parquet' LIMIT 10"   # mini DWH

# 3) Umbrella CLI (arsenal) — scaffold a recipe, then ingest → transform in one go
arsenal init --list                                                  # list recipes
arsenal init csv-cleanup                                             # scaffold a project
arsenal run                                                          # runs arsenal.yaml: ingest → transform
arsenal query "SELECT ..."
```

Three one-command recipes: **github-issues** (API → clean table), **csv-cleanup** (pile of CSVs → dedup/cast), **api-to-postgres** (API → validation gate → PG upsert). `api-to-postgres` needs the postgres extra: `uv tool install "de-arsenal[postgres]"` (or `pugio[postgres]` from source).

To work from source instead, clone the repo and use `uv sync`, then prefix the commands above with `uv run`.

Every supported YAML field is documented straight from the code — [docs/reference/pipeline-schema.md](docs/reference/pipeline-schema.md) · [transform-schema.md](docs/reference/transform-schema.md). Changes are tracked in [CHANGELOG.md](CHANGELOG.md); the honest boundary of what we do and don't guarantee is in [docs/08-limits.md](docs/08-limits.md).

## Docs

Design documents are currently written in Korean; schema references and the changelog are language-neutral.

| Doc | Contents |
|---|---|
| [docs/00-overview.md](docs/00-overview.md) | Vision, positioning, design principles |
| [docs/01-scope.md](docs/01-scope.md) | Build scope — milestones M0–M4, P1/P2 plans, out of scope |
| [docs/02-architecture.md](docs/02-architecture.md) | Monorepo layout, shared core, state model, idempotency strategy |
| [docs/03-tech-stack.md](docs/03-tech-stack.md) | Tech stack choices, rationale, alternatives considered |
| [docs/04-implementation-plan.md](docs/04-implementation-plan.md) | Work breakdown per milestone with completion criteria |
| [docs/05-testing-plan.md](docs/05-testing-plan.md) | Test strategy — unit/integration/E2E, reliability scenarios |
| [docs/06-conventions.md](docs/06-conventions.md) | Coding, commit, branch, PR, CI conventions |
| [docs/07-cost-efficiency.md](docs/07-cost-efficiency.md) | Low-cost high-efficiency design — cost structure, incremental processing, targets |
| [docs/08-limits.md](docs/08-limits.md) | Honest limits — single-node envelope, unsupported scope, per-sink idempotency guarantees |
| [docs/09-oss-leverage.md](docs/09-oss-leverage.md) | Open-source leverage — what we borrow, dlt/dbt interop, adoption criteria |
| [docs/10-direction.md](docs/10-direction.md) | **Direction** — the Canva of data, the AI agent layer (Augur), L1/L2/L3 roadmap, a day in 2027 |
| [docs/tools.md](docs/tools.md) | When to use each tool, quick starts, comparisons |
| [docs/roadmap.md](docs/roadmap.md) | Original roadmap (full problem statements) |

## License

[Apache-2.0](LICENSE)
