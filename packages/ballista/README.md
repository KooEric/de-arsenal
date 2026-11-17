# Ballista

Ballista is the capability-gated remote execution boundary for portable Gladius
`LogicalPlan v1` transforms.  The base install contains profiles, immutable run
manifests, lifecycle interfaces, and a Spark-on-Kubernetes adapter; it does not
install or start Spark, Kubernetes, an object store, or an Iceberg catalog.

The v1 skeleton intentionally refuses production submission unless all inputs
have immutable versions, remote environment checks pass, and the output advertises
an atomic, concurrent-writer-safe commit (currently an Iceberg catalog or an
explicit transactional database output). Raw object-store Parquet is never
presented as exactly-once or atomically published.

```text
ballista plan transform.yaml --profile ballista-k8s.yaml --input-version <snapshot>
ballista run transform.yaml --profile ballista-k8s.yaml --input-version <snapshot>
ballista status <run-id> --state-dir .arsenal
ballista reconcile <run-id> --profile ballista-k8s.yaml --state-dir .arsenal
ballista cancel <run-id> --profile ballista-k8s.yaml --state-dir .arsenal
```

`plan` performs static checks. `run`, `reconcile`, and `cancel` also require a
configured remote immutable blob adapter and verified cluster probe; the base CLI
reports that missing integration instead of pretending a local file write is a
production commit.
