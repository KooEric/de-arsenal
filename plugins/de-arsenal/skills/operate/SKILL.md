---
name: operate
description: Run a DE Arsenal project on a schedule and find out when it fails or goes stale, using cron, Windows Task Scheduler, GitHub Actions or a container. Use for "run this every morning", "alert me when it breaks", "is my data up to date", handling secrets, or "매일 자동으로 돌려줘", "실패하면 알려줘", "데이터가 최신인지 확인해줘".
---

# Operate

The person says when and where ("every morning on this PC, tell me on Slack if
it breaks"); you set it up and show them one test alert.

There is no server or scheduler to install. `arsenal run` is one command that
exits 0 when every step succeeded and 1 otherwise, so anything that runs a
command on a timer can run it. A rerun continues from where the last one
stopped, so a schedule that fires again after a crash is the retry.

## Before scheduling

1. `arsenal check` passes and `arsenal run` succeeds by hand, twice, the second
   doing no new work. Schedule `arsenal check && arsenal run` so a bad hand
   edit fails before any data moves.
2. Secrets are environment variables named in the YAML (`dsn_env: PG_DSN`,
   `token_env: API_TOKEN`, `${VAR}` in a URL). None are written in a file that
   could be committed.
3. `.arsenal/` is on a disk that survives between runs. It records what is
   finished. Losing it does not corrupt or duplicate output, but the next run
   fetches everything again, and an API read in `cursor` mode starts from the
   beginning of its feed.

## Linux or macOS: cron

```cron
PATH=/home/me/.local/bin:/usr/bin:/bin
PG_DSN=postgresql://user:password@db.internal:5432/app

23 3 * * *  cd /srv/pipelines/orders && flock -n .arsenal.lock arsenal run >> run.log 2>&1 || curl -sS -X POST -H 'Content-Type: application/json' -d '{"text":"orders pipeline failed"}' "$SLACK_WEBHOOK_URL"
```

`flock -n` stops a slow run from overlapping the next. macOS has no `flock`;
Arsenal's own lock already refuses a second run of the same pipeline, which
then exits 1 with `pipeline ... is busy`.

Pick an odd minute rather than `0 * * * *`: APIs are busiest on the hour.

## Windows: Task Scheduler

```powershell
$action  = New-ScheduledTaskAction -Execute "arsenal.exe" -Argument "run" -WorkingDirectory "C:\pipelines\orders"
$trigger = New-ScheduledTaskTrigger -Daily -At 3:23am
Register-ScheduledTask -TaskName "orders-pipeline" -Action $action -Trigger $trigger
```

Set credentials as user environment variables (`setx PG_DSN "..."`), then sign
out and in so the task sees them.

## GitHub Actions or a container

Start from the files in the repository: `examples/deploy/github-actions.yml`
and `examples/deploy/Dockerfile`. The one thing to carry over is `.arsenal/`:
the workflow restores and saves it through the Actions cache, and a container
needs it on a mounted volume. GitHub drops caches unread for 7 days, so a job
that runs less than weekly starts from nothing each time.

These two examples are checked for syntax only in the project's CI. Run each
once by hand and watch it before relying on it.

## Knowing it is stale, not only that it failed

A job can succeed every night and still deliver nothing new. Schedule a second
check a little after the run:

```bash
pugio status collect.yaml --max-age-seconds 90000    # exits 1 when stale (0.6.6+)
arsenal status                                       # streams and distributed runs, JSON
```

90000 seconds is 25 hours: a daily job with an hour of slack. Wire the
non-zero exit to the same alert as a failed run.

On 0.6.5 and earlier the command prints the alert but exits 0, so test the
output instead:

```bash
pugio status collect.yaml --max-age-seconds 90000 2>&1 | grep -q "status=stale" && echo STALE
```

## Day-to-day commands

```bash
pugio status collect.yaml --cost     # units done/failed, rows, bytes, last completion
pugio dlq list collect.yaml          # what was set aside and why
tail -50 run.log                     # the last run's output
```

## Housekeeping

Many small runs leave many small Parquet files, which slows queries:

```bash
onager compact --help
onager compact ./data/raw --dry-run      # shows the plan; changes nothing
```

Local directories only. It backs up originals until the rewrite succeeds.

## Tell the person

- What runs, when, and where the log is.
- Where the alert goes, and that they should see one test alert arrive.
- That moving the project to another machine means moving `.arsenal/` with it.
