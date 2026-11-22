---
name: transform
description: Clean and reshape collected data in a DE Arsenal project by writing a Gladius transform YAML that compiles to DuckDB SQL. Use for removing duplicates, fixing types and dates, renaming or dropping columns, filtering rows, computing new columns, joining or aggregating tables, or answering a question from the data. Also "중복 제거해줘", "날짜 형식 맞춰줘", "두 테이블 합쳐줘", "월별로 집계해줘".
---

# Transform

A transform reads Parquet, applies steps in order, and writes Parquet. Each
step becomes one SQL clause, and the SQL can always be shown before it runs.
The person describes the result in words ("drop the cancelled ones, one row
per order, amounts as numbers"); you write the steps and report what changed
in row counts. They do not need to read the YAML.

## Look first

```bash
gladius query "DESCRIBE SELECT * FROM './data/raw/*.parquet'"
gladius query "SELECT * FROM './data/raw/*.parquet' LIMIT 10"
gladius query "SELECT count(*) FROM './data/raw/*.parquet'"
```

## Write the spec

```yaml
name: clean-orders
input: ./data/raw              # a directory of Parquet files
steps:
  - filter: "status <> 'cancelled'"
  - rename: { ord_dt: order_date }
  - cast: { order_date: date, amount: bigint }
  - derive: { amount_krw: "TRY_CAST(replace(amount_text, ',', '') AS BIGINT)" }
  - dedup: [order_id]
  - select: [order_id, customer, order_date, amount_krw]
output: ./data/clean
```

| Wanted | Step |
|---|---|
| Keep only some rows | `filter: "<SQL condition>"` |
| Rename columns | `rename: { old: new }` |
| Change a type | `cast: { col: date }` (DuckDB type names) |
| Add a computed column | `derive: { new_col: "<SQL expression>" }` |
| One row per key | `dedup: [key_col, ...]` |
| Keep and order columns | `select: [a, b, c]` |
| Anything else: joins, group by, window functions | `sql:` (below) |

Steps run top to bottom, so a column must exist before a step uses it: `cast`
before a `filter` that compares dates, `rename` before the new name is used.

## Recipes for dirty values

```yaml
- derive:
    amount: "TRY_CAST(replace(amount_text, ',', '') AS BIGINT)"   # 1,200,000
    memo: "nullif(trim(memo), '-')"                               # '-' means empty
    order_date: "CAST(try_strptime(order_dt, '%Y.%m.%d') AS DATE)" # 2026.10.01
    phone: "regexp_replace(phone, '[^0-9]', '', 'g')"             # digits only
```

`TRY_CAST` and `try_strptime` give NULL where `CAST` and `strptime` would stop
the run on one bad cell. Use them
on real data, then count the NULLs it produced and tell the person how many
values could not be read.

`dedup` keeps one row per key but does not promise which. When "the newest
one" matters, say so in SQL:

```yaml
- sql: >
    SELECT * FROM {input}
    QUALIFY row_number() OVER (PARTITION BY order_id ORDER BY updated_at DESC) = 1
```

## Joins and aggregates

`{input}` is the result of the previous step. Other tables are read by path.

```yaml
- sql: >
    SELECT o.*, c.region
    FROM {input} o
    LEFT JOIN read_parquet('./data/customers/*.parquet') c USING (customer_id)
- sql: >
    SELECT region, date_trunc('month', order_date) AS month,
           count(*) AS orders, sum(amount) AS revenue
    FROM {input}
    GROUP BY ALL
```

After a join, count rows again. A join that multiplies rows means the key is
not unique on the other side; a `LEFT JOIN` that leaves NULLs means keys did
not match (often text against number, or stray spaces).

## Compile, run, check

```bash
gladius compile transform.yaml     # prints the SQL; runs nothing
gladius run transform.yaml
gladius query "SELECT count(*) FROM './data/clean/*.parquet'"
```

Show the compiled SQL when the person wants to see what will happen to their
data. After running, report rows in, rows out, and why they differ (filtered,
deduplicated). An unexplained difference is a bug until shown otherwise.

Register the file under `transforms:` in `arsenal.yaml` so `arsenal run`
includes it. Transforms run in the order listed, after all collection.

## Large inputs that grow

```yaml
incremental: { mode: by_unit }               # new input files are appended
incremental: { mode: by_key, key: [id] }     # new results replace rows with the same key
```

Only new input files are processed. Changing the spec, or changing or deleting
an input file that was already processed, recomputes everything. `by_key` does
not remove rows that were deleted at the source.

## Just a question, no pipeline

```bash
arsenal query "SELECT customer, sum(amount) FROM './data/clean/*.parquet' GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
arsenal query "..." --format csv > result.csv     # table | csv | jsonl
```
