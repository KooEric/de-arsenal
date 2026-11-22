---
name: doctor
description: Diagnose a DE Arsenal pipeline that failed, loaded the wrong number of rows, produced duplicates or NULLs, stopped picking up new data, or reports busy. Use for any error from arsenal, pugio or gladius, and for "행 수가 안 맞아", "중복이 생겼어", "새 파일이 안 들어가", "에러가 나는데 뭔지 모르겠어", "왜 0건이야".
---

# Doctor

For a job using `.arsenal/dataset.duckdb`, inspect `arsenal status`. A failed
publication retains the last successful snapshot. Use the recorded error and
`arsenal dataset preview` to check the corrected inputs before applying again.

Answer in the person's terms: what went wrong with their data, what you
changed, and the numbers now. Keep tool vocabulary out unless they ask.

Start from what the person sees, not from the code. Collect the facts first,
change nothing until the cause is named.

## Facts to collect

```bash
arsenal check                                # is every spec valid? (0.6.6+)
git diff                                     # what was edited since it last worked
arsenal run                                  # the exact error text
pugio status collect.yaml --cost             # done / failed / running, rows
pugio dlq list collect.yaml                  # set aside, with reasons
gladius query "SELECT count(*) FROM './data/raw/*.parquet'"
gladius compile transform.yaml               # the SQL the transform runs
```

Then count at each stage (source, collected, transformed) and find the stage
where the number changes. The cause is there.

## Symptom to cause

| Symptom | Likely cause | Fix |
|---|---|---|
| `invalid pipeline spec ...: sink.type: ...` or `invalid YAML` | A hand edit: misspelled field, wrong indentation, tab character | The message names the field. Fix that line only; keep the rest of their edit |
| `invalid transform spec` with a dozen `Field required` lines for one step | A misspelled step name (`dedupe:` for `dedup:`); the validator lists every step type it tried | Read only the `Extra inputs are not permitted` lines: they name the word that was typed |
| Everything re-collected after an edit | The pipeline's `name` or `state_dir` was changed | Change it back, or accept the re-collection |
| `pipeline '...' is busy (lease held by ...)` | Another run is going, or the last one was killed and its lease has not expired | Wait for the time shown and rerun. Do not delete `.arsenal/` |
| `source.path '...' matches no files` | Wrong path, or run from another directory (paths are relative to where the command runs) | Fix the path or `cd`; `allow_empty: true` only for a folder that is legitimately empty |
| Run succeeds with `fetched=0 written=0` | Everything was already collected. This is the normal second run | Nothing. If new data was expected, see the next rows |
| A file was edited but nothing changed | Files are collected once, by path | `detect_changes: true` with a `duckdb` or `postgres` sink |
| API run succeeds with exactly one page | Server caps page size below `pagination.size` | `stop_on: empty_page` |
| API returns fewer rows than its own total | Wrong `record_path`, or a filter in the URL | Request one page with `curl` and compare |
| Zero rows loaded, no error | `validate` with the default `on_violation: quarantine` set whole files aside | `pugio dlq list`; switch to `on_violation: split` |
| Fewer rows than the source | Rows set aside, a `filter`, or `dedup` on a key that is not unique | Reconcile: loaded + set aside + filtered + deduplicated = source |
| More rows than the source | A join key that is not unique on the other table, or a changed file kept twice in a Parquet sink | Count per key on both sides of the join; use an upsert sink |
| `Conversion Error: Could not convert string` | One cell does not fit the type in `cast` | `TRY_CAST` in a `derive`, then count the NULLs it made |
| Garbled Korean text | Wrong encoding | `source.encoding: cp949` (run the `ingest` profiler) |
| `00123` became `123` | Column inferred as a number in some file | `column_types: { col: string }` |
| `NA` or `-` still present as text | `null_values` applies to numeric and date columns only | `nullif(col, '-')` in a transform |
| Warning that files disagree on a column's type | Type inferred per file | Pin with `column_types` |
| Unit failed with a schema drift message | The source added, renamed or retyped a column | Decide with the person: accept (`warn` once, then back to `block`) or fix upstream |
| `postgres sink requires the 'postgres' extra` | Driver not installed | `uv tool install "de-arsenal[postgres]"` |
| Database source retries for a long time, then fails | A wrong password is retried like a network error | Check the DSN in the environment variable |
| `dlq retry` refused | REST `cursor` or `link` pages cannot be re-requested | Keep the set-aside rows as the record; re-collect from scratch only if they are needed |
| Scheduled run works by hand but not from cron | cron has no `PATH` or environment variables | Set them in the crontab; see `operate` |
| Every scheduled run starts from zero | `.arsenal/` is not kept between runs | Persist it (disk, cache or volume) |

## Starting over, as a last resort

Re-collecting is safe for output: Parquet file names come from the input unit
and database sinks upsert on `merge_key`, so the same data lands on itself.
It costs time and API calls. Ask before doing it, and move rather than delete:

```bash
mv .arsenal .arsenal.bak-$(date +%Y%m%d)
arsenal run
```

For a Parquet sink also move the output directory, or files from units that no
longer exist stay behind.

## What it does not do

Say so plainly instead of working around it:

- One machine. No distributed execution in the base tools.
- No millisecond streaming, no Kafka or database-log CDC.
- File source reads comma-delimited CSV, the first Excel sheet, and JSONL.
- Transform steps cannot delete rows at the destination that were deleted at
  the source.

## Reporting back

Three lines: what was wrong, what was changed, and the numbers that show it is
right now. If the cause is a bug in the tool rather than the spec, say that,
and give the smallest spec and input that reproduce it.
