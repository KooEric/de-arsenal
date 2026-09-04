---
name: arsenal-deploy
description: Take a DE Arsenal pipeline from a laptop to a schedule — GitHub Actions cron, a container for AWS Lambda/Fargate, Cloud Run, or Cloudflare Containers — by composing with the user's cloud provider skill. Use when the user wants a pipeline to run daily/hourly/every N minutes, on a server, in CI, in AWS/GCP/Cloudflare, "when my laptop is closed", or asks how to schedule arsenal run.
---

# arsenal-deploy

DE Arsenal ships no scheduler and hosts nothing. Deployment is a **composition**: this
skill tells the agent what an Arsenal pipeline needs; the user's cloud skill (AWS,
Cloudflare, GCP, …) or GitHub Actions provides compute, secrets and the cron. The user
pays only the bill they already pay.

**If a provider skill is installed, open it alongside this one.** Hand it the contract
below; do not re-derive provider details here.

## What a pipeline needs (the contract)

| Need | Value |
|---|---|
| Runtime | Python 3.11+, `pip install de-arsenal` (+ `[postgres]` / `[dlt]` / `[dbt]` extras as the YAML requires) |
| Command | `arsenal run` in the project dir (reads `arsenal.yaml`), or `pugio run collect.yaml` / `gladius run transform.yaml` |
| Secrets | every env var named in the YAML: `*_env` fields, `dsn_env`, `${VAR}` in headers. Names only — read them out of the YAML, never invent values |
| Exit codes | 0 success; 1 error (line starts with `error:`). Retry policy: re-running is always safe |
| State | `.arsenal/` (pugio) and `.gladius/` (gladius) directories must survive between runs for resume/incremental to work — see below |
| Output | sink path from the YAML: local dir, or `s3://` / `gs://` for parquet (needs provider credentials in env) |
| Resources | one process, DuckDB in-process. Memory scales with batch size; start small (512 MB–2 GB) and read `pugio status --cost` |
| Duration | batch: minutes. Lambda's 15-minute cap fits small pipelines; longer ones go to Fargate/Cloud Run jobs/a VM |

## State persistence (the one hard part, be honest)

Cloud containers are ephemeral. If state is lost, the next run re-fetches everything;
sinks stay duplicate-free but API quota and time are wasted, and REST `cursor`/`link`
pipelines restart from the beginning.

Options today, cheapest honest answer first:

1. **A persistent disk**: a VM, a self-hosted runner, or the user's always-on machine with
   cron. State and data just stay on disk.
2. **Sync state around the run**: before `arsenal run`, copy `.arsenal/` down from S3/R2;
   after (success or failure), copy it back. Two `aws s3 sync` lines; the provider skill
   knows the exact commands. Serialize runs (one at a time) so two copies never race.
3. **Accept re-fetch** for small `file`/`database` pipelines where a full pull is cheap
   (`database` sources re-derive units from `max(key)`).

Native remote state (`state_dir: s3://…`) is on the roadmap (`docs/10-direction.md`, L1).

## Targets

| Target | Shape | Notes |
|---|---|---|
| GitHub Actions | cron workflow, `assets/github-actions.yml` | free for public repos, 2000 min/month private; runner disk is ephemeral → option 2 above; secrets via repo Secrets |
| AWS | container image (`assets/Dockerfile`) on Lambda (≤15 min) or Fargate scheduled task; EventBridge cron; S3 for state sync and parquet sink; Secrets Manager → env | the AWS skill builds these from the contract |
| GCP | Cloud Run job + Cloud Scheduler; GCS sink (`gs://`) | same container |
| Cloudflare | R2 (S3-compatible, zero egress) for parquet + state; compute via Containers + Cron Trigger. **Workers alone cannot run Python DuckDB** | check Containers availability on the user's plan |

## Safety

- Secrets go in the provider's secret store, referenced by name in the workflow/task
  definition. Never in YAML, Dockerfile, workflow file, or commit.
- Before creating any cloud resource, list what will be created and its cost (should be
  within free tier for small pipelines) and get the user's approval. This is the user's
  account and bill.
- Schedule at the frequency the data changes, not faster. Overlapping runs of the same
  pipeline on shared state must be prevented (concurrency = 1).
- First run in the cloud: run once manually, check `pugio status`, then enable the cron.

## Verify

- A manual trigger completes with exit 0 and `done: fetched=… skipped=…`.
- A second trigger reports `skipped` = all (state survived).
- The sink contains data where the user expects it, and `pugio status --cost --max-age-seconds N` does not alert after a scheduled run.
