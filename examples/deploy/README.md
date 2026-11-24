# Running a project on a schedule

Arsenal has no scheduler of its own. `arsenal run` is one process that exits 0 when
every step succeeded and 1 otherwise, so anything that can run a command on a timer
can run it. Three starting points:

| Where | File | State directory (`.arsenal/`) |
|---|---|---|
| Your own server | [crontab.example](crontab.example) | stays on the server's disk |
| GitHub Actions | [github-actions.yml](github-actions.yml) | restored from and saved to the Actions cache |
| Containers (Kubernetes, ECS) | [Dockerfile](Dockerfile) | a persistent volume you mount |

## The one thing to get right: keep `.arsenal/`

The state directory records which pages, files and batches are finished. With it, a
rerun skips finished work. Without it, a run fetches everything again.

Losing it does not corrupt the output of a `postgres` or `duckdb` sink (rows are
upserted on `merge_key`) or of a Parquet sink (file names are derived from the input
unit). It costs time and API calls. The exception is a REST source in `cursor` mode,
which restarts from the beginning of the feed.

GitHub removes caches that have not been read for 7 days and trims a repository's
caches above 10 GB, so a pipeline that runs less than weekly will start from nothing
each time.

## Secrets

Put credentials in environment variables and name them in the YAML (`dsn_env:
PG_DSN`, `Authorization: "Bearer ${API_TOKEN}"`). Do not write them into the YAML.

## Knowing when it failed

All three examples post to a Slack incoming webhook when `arsenal run` exits non-zero.
For a check that also catches "it ran, but the data is stale", schedule
`pugio status <spec> --max-age-seconds 90000` the same way; it exits 1 when the last
success is older than that. `arsenal status` exits 1 when a pipeline has failed or
quarantined units (0.6.12+) or a stream or distributed run needs attention, but does not
check the age of batch pipelines.

## Not verified

The workflow and the crontab line are checked for syntax only, and the Dockerfile has
not been built in this repository's CI. Run each once by hand before relying on it.
