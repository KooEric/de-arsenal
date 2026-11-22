---
name: trust
description: Keep bad data out of a DE Arsenal pipeline's output with validation rules, schema-change detection and data contracts, and handle the rows that were set aside. Use when values look wrong, a source file changed its columns, numbers stopped adding up, someone asks "can I trust this", or "이상한 값 걸러줘", "컬럼이 바뀌었어", "데이터 검증 넣어줘", "격리된 데이터 확인해줘".
---

# Trust

The person says what worries them ("negative amounts shouldn't be there"); you
turn it into a rule, run it, and show them the rows it caught.

Three guards, each a few lines in the collect YAML. Add them once the first
clean run has shown what normal looks like; rules written before seeing the
data are guesses.

## 1. Rules on values

```yaml
validate:
  rules:
    - { field: order_id, not_null: true, unique: true }
    - { field: amount, min: 0, max: 100000000 }
    - { field: status, not_null: true }
  on_violation: split
```

| `on_violation` | Effect | Use when |
|---|---|---|
| `split` | Load the rows that pass; set only the failing rows aside | The normal choice for real data |
| `quarantine` | Set the whole file or page aside, load none of it | One bad row means the file cannot be trusted (a ledger, a settlement file) |
| `block` | Fail the run | Nothing may load unless everything is right |
| `warn` | Load everything, log the violation | Measuring before enforcing |

`quarantine` is the default when `on_violation` is omitted. On real data,
nearly every file has a bad row, and the result is that nothing loads. Write
`split` explicitly unless the whole-file behaviour is wanted.

Derive the rules from the data, not from intuition:

```bash
gladius query "SELECT min(amount), max(amount), count(*) FILTER (amount IS NULL) FROM './data/raw/*.parquet'"
gladius query "SELECT order_id, count(*) FROM './data/raw/*.parquet' GROUP BY 1 HAVING count(*) > 1 LIMIT 10"
```

## 2. Columns changing between runs

```yaml
schema_drift: block      # allow (default) | warn | block
```

Compares column names and types with the previous run. `block` fails the unit
and names the columns that changed; `warn` loads and logs. Limits: it compares
runs, not files within one run (differing types across files in one run only
produce a warning), and for an API it looks at the first page only.

## 3. A contract on the shape

When someone downstream depends on exact columns:

```yaml
contract:
  columns:
    - { name: order_id, type: string, nullable: false }
    - { name: amount, type: int64 }
    - { name: order_date, type: "date32[day]" }
  allow_extra: false
  on_violation: block    # block | quarantine | warn
```

Types are Arrow type strings. Read the real ones off a collected file before
writing them: `gladius query "DESCRIBE SELECT * FROM './data/raw/*.parquet'"`
shows DuckDB names (`BIGINT` is `int64`, `VARCHAR` is `string`, `DOUBLE` is
`double`).

## What was set aside

```bash
pugio dlq list collect.yaml
# 03ca2c9967ed9bcf  emp.csv  split: 1 rows rejected [{'rule': 'min', 'field': '급여', 'count': 1}]
```

The rows themselves are kept, with the reason beside them:

```bash
gladius query "SELECT * FROM '.arsenal/dlq/<pipeline name>/*.parquet'"
```

Show the person the rows and the rule each broke. Then one of:

- **The source was wrong.** They fix the file or the upstream system, then:
  `pugio dlq retry collect.yaml --unit <unit_id>` and `pugio run collect.yaml`.
- **The rule was wrong.** Loosen it, then retry the same way.
- **The rows really are bad.** Leave them. They stay out of the output and on
  record.

`dlq retry` is refused for REST sources in `cursor` or `link` mode: those
pages cannot be requested again by position.

## Proving the output

After any change to rules, give the person a reconciliation they can read:

```
source rows      86,343
loaded           86,340
set aside             3   (max:elevation_ft)
duplicates on key     0
```

Loaded plus set aside must equal the source. If it does not, something else
is dropping rows: go to `doctor`.
