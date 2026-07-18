# Gladius Transform Benchmark — Results

Regression-detection reference, **not a gate**. Numbers below come from one
real run of `benchmarks/bench_transform.py` and are meant to answer "did this
change make transforms N× slower?", not to fail CI. Re-run and update this
file whenever a change plausibly affects `gladius.engine.run_transform` or the
transpiler's generated SQL.

## Run: 2026-07-19

**Machine**

- Apple M3 Pro (12 cores), 36 GB RAM, macOS 26.5.2, arm64
- Disk: local NVMe (APFS), ~111 GiB free at time of run
- Python 3.11.4, DuckDB 1.5.4, PyArrow 24.0.0
- `uv run python benchmarks/bench_transform.py`

**Input**

Synthetic Parquet generated via `duckdb COPY (SELECT ... FROM range(N)) TO
... (FORMAT PARQUET)`. Columns: `id BIGINT`, `amount DOUBLE`, `quantity
INTEGER`, `category VARCHAR` (10 distinct values), `payload VARCHAR` (64-char
concatenated md5 hashes, added purely to bulk up row size toward the ~1GB
target).

- **Target size:** ~1 GB
- **Rows generated:** 13,000,000
- **Actual input size:** 1,010,883,636 bytes (1010.9 MB) — DuckDB default
  compression (no explicit `COMPRESSION` clause). Hit the ~1GB target directly
  (no scale-down needed); measured ~78 bytes/row at this column shape.

**Transform** (scenario-D style: filter + cast + select)

```yaml
steps:
  - filter: "quantity > 50"
  - cast: { amount: double }
  - select: [id, amount, quantity, category]
```

**Results** (3 runs, wall clock via `time.perf_counter`, median reported)

| rows       | input size | run 1  | run 2  | run 3  | median   | throughput      |
|-----------:|-----------:|-------:|-------:|-------:|---------:|-----------------:|
| 13,000,000 | 1010.9 MB  | 0.110s | 0.105s | 0.104s | 0.105s   | ~123.9M rows/sec |

Raw output:

```
generating 13,000,000 rows of synthetic Parquet into <tmp>/input ...
input size: 1010.9 MB (1,010,883,636 bytes)
  run 1/3: 0.110s
  run 2/3: 0.105s
  run 3/3: 0.104s

=== gladius transform benchmark ===
          rows |   input MB |   median s |       rows/sec
    13,000,000 |     1010.9 |      0.105 |    123,934,639
```

### Caveats (read before treating this as "gladius does 1GB in 0.1s")

- The input Parquet file was generated immediately before the timed runs in
  the same process, so it is very likely still resident in the OS page cache
  (macOS unified memory on Apple Silicon makes cached-file reads
  RAM-speed). This benchmark measures **warm-cache** transform throughput,
  not cold-disk I/O.
- DuckDB's Parquet reader does column projection pushdown — the transform
  only touches 4 of 5 columns (`payload` is never read), and the `filter`
  step prunes ~48% of rows before the `select` even applies, so DuckDB likely
  never materializes the full 1GB in memory.
- 12 CPU cores are used (`PRAGMA threads` set to `os.cpu_count()` in
  `gladius/engine.py`), so this reflects fully parallel execution.
- Use this as a same-machine, same-method baseline for regression comparison
  (e.g. "did median time double after this change?"), not as a
  cross-machine or cross-workload performance claim.
