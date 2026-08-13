# DE Arsenal

> **One laptop is a data platform.** One install, one command: ingest → validate → transform → query.

**English** | [한국어](README.ko.md)

A one-command answer to the problems data engineers and analysts hit every day — ingestion jobs that die at 3 AM, expired tokens, piles of CSVs you can't even query. No servers, no clusters, no DAG code.

```bash
uv tool install de-arsenal                    # or: pip install de-arsenal
arsenal init csv-cleanup                      # start from a recipe (github-issues · csv-cleanup · api-to-postgres)
arsenal run                                   # ingest → validate → transform; interrupted? rerun to resume
arsenal query "SELECT * FROM './data/clean/*.parquet' LIMIT 10"
```

> **Status: v0.2.2, live on PyPI.** All 8 packages are published. Ingestion, transformation, the umbrella CLI, and the P1 extensions (dlt/dbt interop, schema drift policies, cloud sinks, Python UDFs, observability, orchestration, backfill, data contracts) work today. The transform package is distributed as `de-gladius`; its Python import and CLI remain `gladius`.

The **completeness** of a Databricks or Snowflake, in the **opposite shape**. While the big platforms do a hundred things at 80%, we do one thing at 100%. Reliability (idempotency, resume, validation) is the default, and the zero-margin cost structure ([docs/07](docs/07-cost-efficiency.md)) falls out of the architecture.

## The armory (internal architecture)

Users only need to know `arsenal`. Underneath are independent tools, one per problem domain — each installable and usable on its own (no lock-in), loosely coupled through an Arrow/Parquet hub.

| Weapon | Domain | Priority | Status |
|---|---|---|---|
| **Arsenal** (umbrella CLI) | Single entry point — init/run/query, 3 one-command recipes | P0 (M4) | ✅ v0.2.2 |
| **Pugio** | Ingestion & loading (ETL engine) | P0 + P1 | ✅ v0.2.2 |
| **Gladius** | Transformation & querying (core processing) | P0 + P1 | ✅ v0.2.2 |
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
| [docs/tools.md](docs/tools.md) | When to use each tool, quick starts, comparisons |
| [docs/roadmap.md](docs/roadmap.md) | Original roadmap (full problem statements) |

## License

[Apache-2.0](LICENSE)
