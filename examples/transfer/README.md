# Large query → Redshift with `pugio transfer` (experimental)

`pugio run` pulls rows through this process as Arrow batches. For a table too large or a
query too slow for that, `pugio transfer` lets the engines move the data:

1. **Export.** The source engine writes the query result to S3 as Parquet.
   - Redshift: `UNLOAD (...) TO 's3://.../' FORMAT AS PARQUET CLEANPATH`
   - Hive: `INSERT OVERWRITE DIRECTORY 's3://.../' STORED AS PARQUET SELECT ...`
2. **Load.** The target Redshift copies those files in, in one transaction:
   `CREATE TEMP TABLE ... (LIKE target)`, `COPY ... FORMAT AS PARQUET`, delete, insert.

No rows pass through the machine running `pugio`. It only sends statements and
records progress.

```bash
uv tool install "de-arsenal[redshift,hive]"
export HIVE_DSN='hive://etl:password@hiveserver2:10000/sales?auth=LDAP'
export DW_REDSHIFT_DSN='redshift://loader:password@dw-cluster:5439/dw'
export DW_COPY_ROLE='arn:aws:iam::123456789012:role/dw-copy'
pugio transfer run examples/transfer/hive-to-redshift.yaml
pugio transfer status examples/transfer/hive-to-redshift.yaml
```

## Units and resuming

`split` cuts the query into half-open ranges `[lo, hi)` of one column, by `day`,
`month` or an integer step. Each range is one unit, with its own S3 folder
(`<staging_path>/<name>/<unit_id>/`), and is exported, loaded and marked done on its own.
Without `split`, the whole query is one unit.

- Rerunning resumes: done units are skipped.
- If the export finished but the load failed, the rerun goes straight to `COPY`. It does
  not run the source query again.
- The run stops at the first unit that still fails after `max_attempts` retries of
  temporary errors (dropped connections). `pugio transfer status` shows that unit's error.
- Changing `query`, or the target's `table`, `dsn_env`, `mode` or `merge_key`, gives new
  unit ids, so every range is exported and loaded again. With `mode: replace` that
  overwrites the earlier result.
- `pugio transfer run --reexport` runs the source query again for units whose export an
  earlier run saved, for example after the staging files expired or the source data
  changed before the load was retried.
- Two runs of the same transfer cannot overlap. The second refuses to start.
- `--start`/`--end` replace `split.start`/`split.end` for one run (`--end` exclusive),
  e.g. a scheduler's data interval. [`airflow/`](airflow/README.md) runs a transfer as a
  daily Airflow DAG this way.

## Modes

| `target.mode` | Per unit | Use when |
|---|---|---|
| `replace` (default) | `DELETE FROM target WHERE <unit range>`, then insert the export. Without `split`, the whole table is replaced | The source is the truth for each range, e.g. daily partitions |
| `merge` | `DELETE FROM target USING stage WHERE <merge_key match>`, then insert | Ranges overlap or rows move between ranges |

A unit whose query returns no rows still clears its range under `replace`. Under `merge`
it changes nothing. For Hive, "no rows" is checked twice: the unit's S3 folder has no data
files, and `SELECT 1 FROM (<query>) LIMIT 1` returns nothing. If the folder is empty but
the query has rows (Hive wrote elsewhere), the unit fails instead of clearing the range.

## Requirements

- **The target table exists** with the columns in the order the query returns them.
  Redshift loads Parquet columns by position, not by name. Hive writes its output
  columns as `_col0`, `_col1`, …, so position is the only mapping there is. List the
  columns explicitly instead of `SELECT *`, in the table's order, each aliased to the
  table's column name.

## Column order check

Before the first export of a run, `pugio transfer run` reads the query's column names
(`SELECT * FROM (<query>) LIMIT 0` on the source) and the target table's columns
(`information_schema.columns`, in ordinal order), and compares them name by name,
position by position, ignoring case. If they differ, it stops before exporting anything
and shows each position that does not match:

```text
error: the query's columns do not match analytics.orders by position (4 in the query, 4 in the table). ...
  position 2: query 'country', table 'city'
  position 3: query 'city', table 'country'
```

This catches a swapped SELECT list, a table recreated in another order, and a column
added to the table but not the query.

The same check then compares **type families** at each position: integer, float,
decimal, string, boolean, date, timestamp, timestamptz. Sizes inside a family pass
(`INT` into `bigint`, `STRING` into `varchar(32)`), and an integer may go into a
decimal. Anything else stops the run:

```text
error: the query's column types do not match analytics.orders:
  position 3 'amount': query DOUBLE (float), table numeric(18,2) (decimal)
Cast in the query (`CAST(expr AS type) AS name`) or change the table. ...
```

`timestamp` and `timestamptz` count as different, because loading one into the other can
shift every value by the time zone offset. Lengths and precision are not compared:
a value too long for its `varchar` or `numeric` fails the unit at `COPY`, and the
transaction rolls back. Types the check does not know (arrays, maps, `SUPER`, `time`,
binary) are logged as not checked and do not stop the run.

The check runs once per run and is skipped when no unit is left. `check_types: false`
compares names only; `check_columns: false` turns off both, for example if `LIMIT 0` is
slow on your Hive version.
- Under `replace`, the target has the `split.column` column under the same name, because
  the delete uses the same range condition as the export.
- `staging_path` is an `s3://` URL that both clusters can reach. The source writes with
  its own permissions (`source.iam_role_env` for Redshift, the Hive cluster's S3
  credentials for Hive). The target reads with `target.iam_role_env`.
- For Hive, the machine running `pugio` lists the unit's S3 folder after the export to
  tell an empty result from a missing one, so it needs read access to `staging_path`
  through the standard AWS credential chain.
- Staging files stay after a successful load. Expire them with an S3 lifecycle rule.
- The target table has no `IDENTITY` or default-only columns: the stage table is
  `LIKE target` and the insert is `SELECT *`. Under `merge`, rows whose `merge_key` is
  NULL never match and are inserted again on each reload.
- `split.column` is written unquoted in both the export and the target delete, so it
  cannot be a reserved word such as `date`.

Limits, and what has and has not been tested, are in
[docs/08-limits.md](../../docs/08-limits.md#pugio-transfer의-보장과-한계-experimental).
