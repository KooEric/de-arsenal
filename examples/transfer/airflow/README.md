# `pugio transfer` as a daily Airflow batch

`dags/pugio_transfer_daily.py` runs [`specs/hive-orders-daily.yaml`](specs/hive-orders-daily.yaml)
once a day on Airflow 3. Each DAG run transfers exactly its own data interval:

```bash
pugio transfer run specs/hive-orders-daily.yaml --start 2026-03-01 --end 2026-03-02
```

`--start`/`--end` replace `split.start`/`split.end` for that run (`--end` is exclusive).
A day's unit has the same id whether it runs alone or as part of a longer range, so a
backfill and the daily runs never load a day twice.

## Setup

1. **Install pugio in its own virtualenv** on every worker, so its dependencies never
   conflict with Airflow's. The DAG calls it as a subprocess via `PUGIO_BIN`
   (default `/opt/pugio/bin/pugio`):

   ```bash
   uv venv /opt/pugio && uv pip install --python /opt/pugio "de-arsenal[redshift,hive]"
   ```

2. **Copy** `dags/` and `specs/` into your DAGs folder, keeping them side by side. The
   DAG finds the spec at `../specs/` relative to itself.

3. **Create the connections and the variable**:

   | Airflow object | Id | Fields |
   |---|---|---|
   | Connection | `hive_sales` | host, port (10000), login, password, schema (database). Extra: `{"auth": "LDAP"}` (`PLAIN`, `NOSASL`, `GSSAPI`, `JWT`) |
   | Connection | `dw_redshift` | host, port (5439), login, password, schema = database name |
   | Variable | `dw_copy_role_arn` | `arn:aws:iam::<account>:role/<name>`, the role the target cluster copies with |

   ```bash
   airflow connections add hive_sales --conn-type generic --conn-host hs2.internal \
     --conn-port 10000 --conn-login etl --conn-password '...' --conn-schema sales \
     --conn-extra '{"auth": "LDAP"}'
   airflow connections add dw_redshift --conn-type generic --conn-host dw.xxxx.redshift.amazonaws.com \
     --conn-port 5439 --conn-login loader --conn-password '...' --conn-schema dw
   airflow variables set dw_copy_role_arn arn:aws:iam::123456789012:role/dw-copy
   ```

4. **Mount a shared volume** at the spec's `state_dir` (`/opt/airflow/pugio-state`) on
   every worker. This is not required for correct results, only for resuming (see below).

5. **Create the target table** in Redshift with the query's columns in the same order.
   The first run stops with a column or type mismatch before exporting anything
   otherwise.

## What a failure looks like

`pugio transfer run` exits 1, the task fails, and Airflow retries it (`retries=2`,
10 minutes apart). The error is in the task log, for example a column check:

```text
error: the query's columns do not match analytics.orders by position (4 in the query, 4 in the table). ...
  position 2: query 'amount', table 'user_id'
```

| Failure | What the retry does |
|---|---|
| Connection dropped during the Hive export | pugio already retried it `max_attempts` times in process; the Airflow retry runs the export again |
| Export done, `COPY` failed | With a shared `state_dir`, it goes straight to `COPY`. Without one, it exports again |
| Column or type mismatch, missing table, bad credentials | The same error again. Fix the cause, then clear the task |

`pugio transfer status specs/hive-orders-daily.yaml --start 2026-03-01 --end 2026-03-02`
on a worker shows that day's unit state and its last error.

## Choices in this DAG

- **`max_active_runs=1`.** Runs share one state file and one target table. Two concurrent
  `DELETE`/`INSERT` transactions on the same Redshift table can abort each other with a
  serializable isolation error.
- **`catchup=False`.** To load past days, run
  `airflow backfill create --dag-id pugio_transfer_hive_orders_daily --from-date 2026-01-01 --to-date 2026-02-01`.
  Each day is still its own run and its own unit.
- **Secrets at run time, not in templates.** Connections are read inside the task,
  the DSNs are registered with `mask_secret`, and they reach pugio as environment
  variables. A templated `env=` on a `BashOperator` would show the rendered DSN, password
  included, under "Rendered Template".
- **Dates in UTC.** `data_interval_start` is UTC. If `order_date` is in another time
  zone, set `start_date` with that `tz` so the interval dates line up with the column.

Airflow 2 needs different imports; the DAG's docstring lists them.
