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

## Install the portable skill

From the repository root run `python3 scripts/build_skill.py`. This builds
`dist/de-arsenal.zip` with one entry point, the seven guides and their helpers.

- In ChatGPT, use **Plugins → Skills → Create → Upload from your computer** if enabled for your workspace. [ChatGPT Skills](https://help.openai.com/en/articles/20001066-skills-in-chatgpt)
- In Claude, use **Customize → Skills → + → Create skill → Upload a skill** and enable it. [Claude Skills](https://support.claude.com/en/articles/12512180-use-skills-in-claude)
- For Codex, extract the ZIP into the data project's `.agents/skills/` directory. Its entry point is `.agents/skills/de-arsenal/SKILL.md`.

In a shell-enabled session, try “샘플 CSV로 프로젝트를 만들고 중복 제거 전후 건수를 보여 줘.”
Install the CLI on the execution machine with `uv tool install de-arsenal==0.7.1`.
A recurring job needs a persistent project directory on that machine.

## What was checked

Every command and spec in these skills was run against DE Arsenal 0.6.5 and the 0.6.6 source tree on
Linux: CP949 CSV with leading zeros and `NA`, a tab-delimited file with title
lines, validation with `split` and `quarantine`, DLQ list and retry, schema
drift, a contract, a DuckDB sink, SQL steps with a join and an aggregate, and
the staleness check, and `arsenal check` on valid and broken specs. REST and database sources, cron, Task Scheduler, GitHub
Actions and the container were written from the schema reference and
`examples/deploy` and were not run here.

Whether an agent picks the right skill for a given sentence has not been
measured.
