# Hasta (`de-hasta`)

Leased REST and Postgres outbox microbatch ingestion with durable checkpoints
and deterministic batch identities. CDC v1 consumes immutable `I`/`U`/`D`
outbox envelopes ordered by `sequence`, then applies them transactionally to
DuckDB or Postgres with an event-id ledger for replay safety.

`hasta run stream.yaml --once` processes at most one available batch. A table
cursor declaration is validated conservatively but is not treated as CDC: it
cannot observe hard deletes, so use an outbox when deletes matter. Kafka,
binlog/WAL or other log-based CDC, raw SQL, and long-running daemon supervision
are not part of v1.
