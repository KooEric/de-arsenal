---
name: arsenal-quality
description: Add data quality gates to DE Arsenal pipelines — validate rules (not_null, unique, min, max), Arrow-typed data contracts, schema drift policy, quarantine/DLQ handling, and freshness alerts — and choose block vs quarantine vs warn. Use when the user asks about validation, data contracts, schema changes, bad rows, dead letter queue, data freshness/SLA, or "how do I know the data is right".
---

# arsenal-quality

Quality in DE Arsenal is not a separate framework. It is a gate inside the pipeline,
evaluated on every batch right before the sink writes. Three layers, all declared in
`collect.yaml`:

| Layer | Field | Checks | Default policy |
|---|---|---|---|
| Row rules | `validate.rules` | `not_null`, `unique`, `min`, `max` per field | `quarantine` |
| Contract | `contract.columns` | column exists, Arrow type (`int64`, `string`, …), `nullable`, `allow_extra` | `block` |
| Drift | `schema_drift` | field names/types/nullability vs last snapshot | `allow` |

Policies: `block` fails the unit (pipeline stops on it), `quarantine` moves the unit to the
DLQ and the run continues, `warn` logs and writes.

## Inspect first

- Profile the raw data before proposing rules. Run the pipeline once to Parquet, then:
  `arsenal query "SELECT count(*), count(DISTINCT id), count(*) FILTER (WHERE amount IS NULL) FROM './data/x/*.parquet'"`
  and `DESCRIBE SELECT * FROM './data/x/*.parquet'`. Rules must come from observed data,
  not assumptions.
- Ask what "wrong" costs: if a bad row in the sink is expensive (finance, billing) use
  `block`; if continuity matters more, use `quarantine` and review the DLQ.

## Declaring gates

```yaml
validate:
  rules:
    - { field: order_id, not_null: true, unique: true }
    - { field: amount, not_null: true, min: 0 }
  on_violation: quarantine
contract:
  columns:
    - { name: order_id, type: int64, nullable: false }
    - { name: amount, type: double }
  allow_extra: false
  on_violation: block
schema_drift: block          # allow | warn | block
```

Notes:

- `unique` is checked within the batch (unit); cross-unit uniqueness is the sink's
  `merge_key` job (`duckdb`/`postgres` upsert) or a `dedup` step in `arsenal-transform`.
- Drift compares the first non-empty batch of a run to the last snapshot. `warn` records
  the new snapshot; `block` fails the unit so a human decides.
- Transform-side checks live in `arsenal-transform`: `dedup`, `filter`, and row-count
  queries before/after.

## Working the DLQ

```bash
pugio dlq list collect.yaml                 # unit_id  unit_key  reason
pugio dlq retry collect.yaml --unit <unit_id>   # requeue; next run re-fetches it
```

Fix the cause first (rule too strict? source bug?), then retry. `dlq retry` is refused for
REST `cursor`/`link` pagination: a forward-only cursor cannot regenerate a past page. Keep
the DLQ file as evidence and re-collect with a new pipeline name if the data is needed.

## Freshness

```bash
pugio status collect.yaml --cost --max-age-seconds 3600
```

Prints cumulative rows/bytes/time and last completion; if older than the threshold it
writes `alert: freshness …` to stderr. This is the hook for "is the data late" checks in a
scheduler; there is no built-in notifier — the scheduler or a cloud skill sends the alert.

## Safety

- Start new pipelines with `quarantine`, not `block`, unless the user says a bad row is
  worse than a late pipeline. Tighten after the first DLQ review.
- Never loosen a rule or switch `block` → `warn` just to make a run pass. Report the
  violation and let the user decide.
- Do not delete DLQ files; they are the evidence.

## Verify

- Run once with rules on. `pugio status` shows `quarantined` matches `dlq list`.
- Query the sink for the invariant: `SELECT count(*) FROM … WHERE amount < 0` returns 0.
- Change nothing and re-run: no new quarantines, all units skipped.
