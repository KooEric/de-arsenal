# DE Arsenal skills

Agent skills that let someone build and run a data pipeline by describing it in
words. The agent inspects the data, writes the DE Arsenal specs, runs them, and
reports back with row counts. The specs stay ordinary YAML files: anyone can
edit them by hand, the agent re-reads them before changing anything, and
`arsenal check` validates them after either has.

```text
/plugin marketplace add KooEric/de-arsenal
/plugin install de-arsenal@de-arsenal
```

Then say what you want:

> 이 폴더의 엑셀 파일들을 하나로 합치고 중복을 빼 줘.
> Pull our orders API into Postgres every morning and tell me on Slack if it fails.
> 지난달 거래처별 매출을 보여 줘.

| Skill | When it is used |
|---|---|
| `start` | Any request that begins with data and a wish. Runs the conversation and picks the others |
| `ingest` | Reading files, APIs and database tables. Includes a file profiler |
| `transform` | Cleaning, deduplicating, joining, aggregating |
| `ask` | Questions about the data, answered in words |
| `trust` | Validation rules, column-change detection, rows set aside |
| `operate` | Schedules, secrets, failure and staleness alerts |
| `doctor` | Errors and wrong numbers, by symptom |

Each skill is a `SKILL.md` in the Agent Skills format, so the `skills/`
directory can also be copied into any agent that reads that format. The
profiler path in `ingest` uses `${CLAUDE_PLUGIN_ROOT}`; elsewhere, point it at
`skills/ingest/scripts/profile_file.py`.

## What was checked

Every command and spec in these skills was run against DE Arsenal 0.6.5 on
Linux: CP949 CSV with leading zeros and `NA`, a tab-delimited file with title
lines, validation with `split` and `quarantine`, DLQ list and retry, schema
drift, a contract, a DuckDB sink, SQL steps with a join and an aggregate, and
the staleness check, and `arsenal check` on valid and broken specs. REST and database sources, cron, Task Scheduler, GitHub
Actions and the container were written from the schema reference and
`examples/deploy` and were not run here.

Whether an agent picks the right skill for a given sentence has not been
measured.
