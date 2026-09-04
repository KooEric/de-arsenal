---
name: arsenal-transform
description: Write and run declarative Parquet transformations with DE Arsenal (gladius) — filter, rename, cast, select, dedup, derive, SQL and Python steps compiled to DuckDB SQL — and run ad-hoc SQL over Parquet with arsenal query. Use when the user wants to clean, reshape, deduplicate, cast, join, or query collected data, mentions gladius, transform.yaml, or asks to see the SQL a transform will run.
---

# arsenal-transform

`gladius` (PyPI: `de-gladius`, also `arsenal transform`) compiles a small YAML
(`TransformSpec`) into one DuckDB SQL statement and runs it in-process. There is no
hidden engine: `gladius compile` prints the exact SQL. Field reference:
`references/transform-schema.md`.

## Scope

- Author `transform.yaml`: `input` (Parquet dir or glob) → ordered `steps` → `output`.
- Show the compiled SQL, run it, verify with queries.
- Ad-hoc analysis: `arsenal query "<sql>"` / `gladius query` over any Parquet path.
- Not this skill: ingestion (`arsenal-pipeline`), validation policy (`arsenal-quality`).

## Inspect first

1. Where is the input? Usually a pugio sink dir like `./data/issues` (many
   `<unit_id>.parquet` files). Confirm columns and types before writing steps:
   `arsenal query "DESCRIBE SELECT * FROM './data/issues/*.parquet'"` and
   `arsenal query "SELECT * FROM './data/issues/*.parquet' LIMIT 5"`.
2. Is there an `arsenal.yaml`? Add the transform under `transforms:` so `arsenal run`
   executes it after collection.

## Writing transform.yaml

The ladder is deliberate: use the simplest rung that expresses the logic.

```yaml
name: clean-issues
input: ./data/issues
steps:
  - filter: "state IS NOT NULL"          # SQL boolean expression
  - rename: { created_at: opened_at }
  - cast: { number: bigint }             # types: bigint integer double varchar boolean date timestamp decimal
  - dedup: [number]                      # keep one row per key
  - derive: { year: "date_part('year', opened_at)" }   # SQL scalar expressions
  - select: [number, title, opened_at, year]
output: ./data/issues_clean
```

Escape hatches, in order:

- `sql: "SELECT … FROM {input} …"` — `{input}` is the previous step as a CTE.
- `python: "pkg.module:func"`, `args: [col, …]`, `output: new_col` — a scalar UDF;
  needs `pip install "de-gladius[python]"`; not vectorised, no side effects guaranteed.
- `map: { new_col: "expr", … }` at top level adds derived columns before `steps`.

Incremental re-runs: `incremental: { mode: by_unit }` appends only new input files;
`mode: by_key` with `key: [id]` lets new results overwrite old keys. Any changed or deleted
input file, or a changed spec, triggers a full recompute (state in `.gladius/`).

## Run

```bash
gladius compile transform.yaml     # print the SQL — read it before running
gladius run transform.yaml         # writes Parquet to output
arsenal query "SELECT count(*) FROM './data/issues_clean/*.parquet'"
arsenal query "..." --format csv|jsonl   # machine-readable output
```

## Safety

- A transform spec runs SQL with the user's privileges. Expressions are not validated
  (only `;` is rejected). Never put file-reading subqueries (`read_csv('/etc/…')`) or
  paths outside the project into expressions. Treat a spec like code.
- Always `compile` and show the SQL before the first `run` of a new or edited spec.
- `output` is replaced on each full run. Do not point it at the input directory.

## Verify

- Row counts in vs out, and that the difference is explained by `filter`/`dedup`:
  `arsenal query "SELECT count(*) FROM './data/issues/*.parquet'"` vs the output.
- Types: `DESCRIBE SELECT * FROM './data/issues_clean/*.parquet'`.
- A second `gladius run` on unchanged input is fast and produces identical output.
