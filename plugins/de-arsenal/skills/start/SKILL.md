---
name: start
description: Turn a request about data into a working pipeline whose YAML specs both the person and the agent can edit. Use whenever someone describes data they have (CSV or Excel files, an API, a database) and what they want from it (collected, cleaned, combined, loaded somewhere, kept up to date) in ordinary words, with or without an existing DE Arsenal project. Also "where do I begin", "데이터 파이프라인 만들어줘", "이 파일들 정리해서 DB에 넣어줘", "매일 자동으로 받아오게 해줘", "이 폴더 엑셀들 하나로 합쳐줘", and when the person has edited a spec by hand ("YAML 고쳤는데 봐줘", "이 설정 바꿔도 돼?").
---

# Start: from words to a working pipeline

The person can talk, edit the YAML themselves, or both. Either way they should
end up with a table they can trust, specs they can read, and a short account of
what happened to their data. Nobody is required to learn what a sink or a merge
key is before getting a result, and nobody is locked out of the files either.

## Review and publish changing local data

For a folder of CSV, JSONL, Parquet or Excel files (Excel needs the `excel` extra), use `arsenal dataset --help` to
check whether the installed CLI includes the dataset workflow. It is available
from DE Arsenal 0.6.7. Use a separate working directory for each job.
Existing API and database projects can review their collected Parquet files here.

1. Run `arsenal dataset preview --files 'input/*.csv'`. Keep repeated observations
   by default. Add `--distinct` for agreed exact-row deduplication, `--key`
   for a known unique identifier, `--measure` for a meaningful numeric total,
   and `--time-column` for the date range. Set `--max-age-hours` only when the
   user expects input contents to change on that cadence.
2. Explain the source/result counts, optional totals, and the actual excluded
   or revised examples relevant to the user. Without a key, changes are row
   additions/removals including duplicate counts. Ask about ambiguous identity
   or unexpected removals before publishing.
3. Publish with `arsenal dataset apply`, repeating the preview options and adding
   `--expect <version from preview>`. The successful run saves those rules.
   The next preview needs no options. A removed source file removes its rows
   from the next snapshot. If all files are missing, publication fails.
4. Read `arsenal status` in the job folder for last success, observed data period,
   input freshness and recovery. A failed apply preserves the last published
   table. To answer questions run `arsenal query "ATTACH '.arsenal/dataset.duckdb'
   AS ds (READ_ONLY); SELECT * FROM ds.published LIMIT 10"` as one command.
   Keep this DuckDB file on persistent storage.

## How the conversation goes

1. **They say what they want**, in their own words.
2. **You look before asking.** List the folder, open the file, call the API
   once. Most questions answer themselves from the data.
3. **You ask only what the data cannot tell you**, one question at a time, as a
   choice between concrete options in their terms.
4. **You build and run it**, then report in plain language with numbers.
5. **They react in words** ("the dates look wrong", "drop the cancelled ones"),
   and you change it and report again.

### Questions, in their words

| You need | Ask like this | Not like this |
|---|---|---|
| Key column | "Does one 주문번호 always mean one order, or can it repeat?" | "What is the merge key?" |
| Duplicates | "Row 12 and row 40 are identical. Keep one?" | "Should I dedup?" |
| Bad values | "3 rows have a negative amount. Leave them out and show you, or stop?" | "Which on_violation policy?" |
| Destination | "Keep the result as files here, or put it in your Postgres?" | "Which sink type?" |
| Frequency | "Is this a one-time cleanup, or do new files keep coming?" | "Do you need scheduling?" |

Do not ask what has a safe default. Defaults: result as Parquet files under
`./data`, run by hand, bad rows set aside and shown rather than dropped
silently or stopping the run.

### Reporting, in their words

Say what happened to the data, with counts they can check:

> 엑셀 3개에서 4,812행을 읽었습니다. 완전히 같은 행 37개를 하나씩만 남겼고,
> 금액이 음수인 3행은 따로 빼 두었습니다(아래 표). 결과는 4,772행이고
> `data/clean`에 있습니다. 사번의 앞자리 0은 그대로 유지했습니다.

Then name the files you created or changed and what changed in each, in one
line apiece, so they can open and edit them. Then anything they should decide,
as a question. When they ask how a result was produced, show the compiled SQL
(`gladius compile`), which is the exact statement that ran.

Use their language and their column names. Translate tool words on the way
out: unit → file or page, quarantined → set aside, merge key → the column that
identifies a row, sink → where it ends up, schema drift → the columns changed.

## The YAML is shared

The specs are ordinary files the person owns. They may edit them by hand, in
review, or from another tool. Work as a colleague would on a shared file.

**When you write a spec**

- Write it to be read: a comment in the person's language on any setting whose
  reason is not obvious (why this encoding, why this key, why `split`).
- First line of each file, so their editor completes and checks fields:
  `# yaml-language-server: $schema=https://raw.githubusercontent.com/KooEric/de-arsenal/main/schemas/pipeline.json`
  (`transform.json` for transforms).
- One pipeline or transform per file, named for what it holds
  (`collect-orders.yaml`, `clean-orders.yaml`).

**When you change a spec**

- Read it from disk first, every time. It may have changed since you wrote it.
- Make the smallest edit. Never regenerate the file: their comments, ordering
  and hand-tuned values stay.
- If their edit differs from what you would have written, theirs stands. Speak
  up only when it loses data or cannot run, and say why.

**After anyone edits**

```bash
arsenal check          # validates every spec; runs nothing, writes nothing
```

On versions without `check` (0.6.5 and earlier): `gladius compile <transform>`
for a transform, and `pugio status <spec>` for a pipeline; both load and
validate the file without running it.

When a hand edit is invalid, show the error against the field, explain it in
plain words, and offer the fix. Do not silently rewrite their change.

**Changes that need a yes first**

These look small in the file and are not small in effect. Say what will happen
and wait for agreement:

| Edit | Effect |
|---|---|
| `name` of a pipeline | New bookkeeping file: everything is collected again |
| `state_dir`, or deleting `.arsenal/` | Same |
| `merge_key` | Rows are matched differently; old rows may be left behind |
| Sink `path` or `table` | Output goes somewhere new; the old output stays as it was |
| Removing or loosening `validate`, `contract`, `schema_drift` | Rows that were held back start loading |
| `pugio dlq retry` | Set-aside rows are re-collected and their record is removed |

**Keep it reviewable**

Put the project in git: commit the specs, ignore `data/`, `.arsenal/` and
`*.duckdb`. A change to a pipeline is then a diff someone can review, and a bad
change can be reverted. Offer this the first time you create a project; in a
team, propose changes on a branch rather than editing what is scheduled.

## Building it

Install once, quietly:

```bash
uv tool install de-arsenal        # or: pip install de-arsenal   (Python 3.11+)
```

Extras only when the destination needs them: `"de-arsenal[postgres]"`,
`[redshift]`, `[iceberg]`.

Scaffold from the closest recipe, or write the three files directly:

```bash
arsenal init --list
arsenal init csv-cleanup --dest my-project     # folder of CSVs -> dedup, cast
arsenal init github-issues --dest my-project   # REST API -> cleaned table
arsenal init api-to-postgres --dest my-project # REST API -> validate -> Postgres upsert
```

```yaml
# arsenal.yaml: what runs, in order
name: my-project
pipelines:        # collection, one YAML each
  - collect.yaml
transforms:       # cleaning and shaping, one YAML each
  - transform.yaml
```

Then use the skill for the part being built:

| The person said something like | Skill |
|---|---|
| "read these files", "pull from this API", "copy this table" | `ingest` |
| "remove duplicates", "fix the dates", "join these", "total by month" | `transform` |
| "how many…", "which customer…", "show me…" | `ask` |
| "filter out the weird ones", "can I trust this", "the columns changed" | `trust` |
| "do this every morning", "tell me if it breaks" | `operate` |
| "it failed", "the count is off", "nothing loaded" | `doctor` |

## Prove it before reporting

```bash
arsenal check
arsenal run
arsenal query "SELECT count(*) FROM './data/clean/*.parquet'"
arsenal query "SELECT * FROM './data/clean/*.parquet' LIMIT 10"
arsenal run          # again: must do no new work (fetched=0 or skipped=N)
```

Compare the output count with the source count and account for the
difference. "It ran without errors" is not evidence; matching numbers are.
The clean second run is what makes it safe to repeat or schedule.

## Using it at work

- **Try it on a copy first.** First runs go to local Parquet or a scratch
  table, never straight into a table other people read. Point it at the real
  destination only after the numbers reconcile.
- **Credentials.** Environment variables only, named in the YAML (`dsn_env`,
  `token_env`, `${VAR}`). Ask the person to set them; never ask them to paste a
  secret into the chat or a file. A key written into a `url` ends up in the
  state file and logs. Use a database account that can write only the target
  tables, and a read-only one for sources.
- **Writing to a shared database is a decision.** Before the first run against
  it, state the table, the key rows are matched on, and that existing rows with
  the same key will be replaced. Wait for a yes.
- **Stay on the stable tools** for anything people depend on: Arsenal, Pugio
  and Gladius keep their YAML fields within a minor version. Hasta, Ballista,
  Onager are beta; Aquila, Augur, Pilum and the Redshift and Iceberg sinks can
  change in any release. Pin the installed version (`de-arsenal==0.6.14`) on
  the machine that runs the schedule.
- **Personal data.** Collected rows, set-aside rows under `.arsenal/dlq/` and
  the output are all plain files on this disk. Say so when the data has names,
  phone numbers or the like, so the person can place the project accordingly.

## Be straight about the edges

- One machine. Tens of millions of rows are fine; data that does not fit on
  one disk is not what this is for.
- Everything is files in the project directory: the specs, the output under
  `./data`, and the bookkeeping under `.arsenal/`. Deleting `.arsenal/` makes
  the next run start over.
- When the tool cannot do something, say so and offer the nearest thing that
  works, rather than faking it.
