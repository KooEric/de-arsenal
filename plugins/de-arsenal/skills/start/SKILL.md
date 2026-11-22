---
name: start
description: Turn a spoken request about data into a working pipeline. Use whenever someone describes data they have (CSV or Excel files, an API, a database) and what they want from it (collected, cleaned, combined, loaded somewhere, kept up to date) in ordinary words, with or without an existing DE Arsenal project. Also "where do I begin", "데이터 파이프라인 만들어줘", "이 파일들 정리해서 DB에 넣어줘", "매일 자동으로 받아오게 해줘", "이 폴더 엑셀들 하나로 합쳐줘".
---

# Start: from words to a working pipeline

The person talks. You build. They never have to read or write YAML, learn
what a sink or a merge key is, or run a command themselves. Those are how you
get the work done; what they get is a table they can trust and a short account
of what happened to their data.

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

Then anything they should decide, as a question. Then nothing else. Keep the
YAML, SQL and commands out of the report unless they ask "how" or "show me";
when they do, show the compiled SQL (`gladius compile`), which is the exact
statement that ran.

Use their language and their column names. Translate tool words on the way
out: unit → file or page, quarantined → set aside, merge key → the column that
identifies a row, sink → where it ends up, schema drift → the columns changed.

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
arsenal run
arsenal query "SELECT count(*) FROM './data/clean/*.parquet'"
arsenal query "SELECT * FROM './data/clean/*.parquet' LIMIT 10"
arsenal run          # again: must do no new work (fetched=0 or skipped=N)
```

Compare the output count with the source count and account for the
difference. "It ran without errors" is not evidence; matching numbers are.
The clean second run is what makes it safe to repeat or schedule.

## Be straight about the edges

- One machine. Tens of millions of rows are fine; data that does not fit on
  one disk is not what this is for.
- Everything is files in the project directory: the specs, the output under
  `./data`, and the bookkeeping under `.arsenal/`. Deleting `.arsenal/` makes
  the next run start over.
- Passwords and tokens go in environment variables. Ask the person to set
  them; never ask them to paste a secret into the chat or a file.
- When the tool cannot do something, say so and offer the nearest thing that
  works, rather than faking it.
