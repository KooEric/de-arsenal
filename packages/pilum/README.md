# Pilum (`de-pilum`)

**Experimental.** Reverse ETL for a DE Arsenal repo: send records from a Parquet
dataset (or a DuckDB query over one) to an HTTP endpoint or a Slack webhook, and
send each change once. The spec format, the ledger file and the CLI may change in
any 0.x release.

Each run compares the source with a local ledger of what the destination already
accepted, and sends only the difference:

| `mode` | Sends |
|---|---|
| `insert` | records whose key is new |
| `upsert` (default) | new keys, and keys whose content changed |
| `mirror` | the above, plus a delete request for keys that left the source |

```yaml
# churn.yaml
name: churn-alerts
source:
  type: parquet
  path: data/marts/churn_risk
key: [customer_id]
destination:
  type: http
  url: https://api.example.com/v1/alerts
  headers: { Authorization: "Bearer ${ALERTS_TOKEN}" }
  body: { id: "{customer_id}", score: "{risk_score}", note: "risk for {customer_id}" }
  idempotency_header: Idempotency-Key
  rps: 5
```

```bash
pip install de-pilum
pilum plan churn.yaml       # counts and sample keys; sends nothing, writes nothing
pilum run churn.yaml        # exits 1 if any record was not delivered
pilum status churn.yaml     # ledger size, last run, dead letters; exits 1 on alerts
pilum replay churn.yaml     # put dead letters back in line for the next run
pilum reset churn.yaml --confirm   # forget what was sent; next run sends everything
```

## What it guarantees, and what it does not

- **A record is recorded as sent only after a 2xx response.** If the process dies
  between the response and the ledger write, the next run sends that record again
  with the same `Idempotency-Key`.
- **Delivery is at-least-once.** It is exactly-once only when the endpoint
  deduplicates on the idempotency header. Slack webhooks do not, so a crash at
  the wrong moment can post one message twice.
- **The ledger is the only memory.** Pilum does not read the destination. If
  something else changes the destination, Pilum does not notice; `reset` and
  re-send is the way to resynchronise.
- **Rotating a token or changing the URL does not re-send anything.** The
  dispatch id is derived from the dispatch name, the record key and the record
  content only.

## Failure handling

| Response | What happens |
|---|---|
| 2xx | recorded in the ledger |
| 408, 429, 5xx, network error | retried with backoff (`retry.max_attempts`); if every try fails the record stays queued for the next run, and moves to the dead-letter queue once its failed tries reach `dlq.after_attempts` |
| other 4xx | the record goes straight to the dead-letter queue with the response text |
| 401, 403 | the run stops; nothing is counted against the records |
| `safety.max_consecutive_failures` records fail in a row | the run stops: the destination is down |

A dead letter is skipped until `pilum replay`. If its source record changes, the
new version is sent normally and the dead letter is dropped.

## Safety rails

- **First run.** With an empty ledger, a run that would send more than
  `safety.max_first_dispatch` (1,000) records is refused unless
  `--confirm-initial-load` is given.
- **Deletes.** A `mirror` run that would delete more than
  `safety.max_delete_fraction` (50%) of the ledger is refused unless
  `--confirm-deletes` is given. An empty or mis-pointed source otherwise looks
  like "every record was removed".
- **One sender.** A second `run`, `replay` or `reset` on the same dispatch is
  refused while one is in progress.
- **Secrets.** `${ENV_VAR}` in `url` and `headers` is read when a request is
  sent. It is never written to the ledger or shown by `status`, and it is masked
  in error messages. `${...}` is not expanded inside `body` or Slack `text`.

## Not in this version

Bulk (multi-record) requests, OAuth token refresh, named SaaS connectors,
event-driven dispatch from a Hasta outbox, and `arsenal run` / Aquila
integration. Field reference: [dispatch-schema.md](https://github.com/KooEric/de-arsenal/blob/main/docs/reference/dispatch-schema.md).
