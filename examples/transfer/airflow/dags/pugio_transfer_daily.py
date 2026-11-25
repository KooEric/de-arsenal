"""Daily Hive → Redshift transfer with `pugio transfer`, one DAG run per day (Airflow 3).

Each run transfers its own data interval: ``--start`` is the interval start date and
``--end`` the interval end date (exclusive), so a run loads exactly one day and a
backfill loads each day once. ``pugio transfer`` exits 1 on any failure, the task
fails, and Airflow retries it; a retry resumes where the last attempt stopped.

Secrets are read from Airflow connections when the task runs, registered with the
secrets masker, and handed to the subprocess as environment variables. They never
appear in rendered templates or in the command line.

Airflow 2: import ``dag``/``task``/``get_current_context`` from ``airflow.decorators``
and ``airflow.operators.python``, read connections with
``airflow.hooks.base.BaseHook.get_connection``, and ``mask_secret`` from
``airflow.utils.log.secrets_masker``.
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

import pendulum
from airflow.sdk import Connection, Variable, dag, get_current_context, task
from airflow.sdk.log import mask_secret

log = logging.getLogger(__name__)

# `pugio` lives in its own virtualenv so its dependencies never meet Airflow's:
#   uv venv /opt/pugio && uv pip install --python /opt/pugio "de-arsenal[redshift,hive]"
PUGIO_BIN = os.environ.get("PUGIO_BIN", "/opt/pugio/bin/pugio")
SPEC = Path(__file__).resolve().parent.parent / "specs" / "hive-orders-daily.yaml"

HIVE_CONN_ID = "hive_sales"  # Connection type: generic. Extra: {"auth": "LDAP"}
DW_CONN_ID = "dw_redshift"  # Connection type: generic or redshift. Schema = database
COPY_ROLE_VARIABLE = "dw_copy_role_arn"  # arn:aws:iam::<account>:role/<name>


def _userinfo(conn: Connection) -> str:
    if not conn.login:
        return ""
    password = f":{quote(conn.password, safe='')}" if conn.password else ""
    return f"{quote(conn.login, safe='')}{password}@"


def redshift_dsn(conn_id: str) -> str:
    conn = Connection.get(conn_id)
    dsn = f"redshift://{_userinfo(conn)}{conn.host}:{conn.port or 5439}/{conn.schema}"
    mask_secret(dsn)  # the URL-encoded form differs from the password Airflow masks
    return dsn


def hive_dsn(conn_id: str) -> str:
    conn = Connection.get(conn_id)
    auth = conn.extra_dejson.get("auth", "PLAIN")
    database = conn.schema or "default"
    dsn = f"hive://{_userinfo(conn)}{conn.host}:{conn.port or 10000}/{database}?auth={auth}"
    mask_secret(dsn)
    return dsn


def run_pugio(args: list[str], env: dict[str, str]) -> None:
    """Stream pugio's output into the task log; raise on a non-zero exit."""

    log.info("running: pugio %s", " ".join(args))
    with subprocess.Popen(
        [PUGIO_BIN, *args],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as process:
        assert process.stdout is not None
        for line in process.stdout:
            log.info("%s", line.rstrip())
    if process.returncode != 0:
        raise RuntimeError(f"pugio exited with {process.returncode}; see the log above")


@dag(
    dag_id="pugio_transfer_hive_orders_daily",
    schedule="@daily",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,  # backfill past days with `airflow backfill create`
    # One run at a time: runs share one state file and one target table, and two
    # concurrent DELETE/INSERT on the same Redshift table can abort each other.
    max_active_runs=1,
    tags=["pugio", "transfer"],
)
def pugio_transfer_hive_orders_daily() -> None:
    @task(
        retries=2,
        retry_delay=timedelta(minutes=10),
        execution_timeout=timedelta(hours=6),  # the Hive export of one day can be long
    )
    def transfer() -> None:
        context = get_current_context()
        start = context["data_interval_start"].date().isoformat()
        end = context["data_interval_end"].date().isoformat()
        env = {
            **os.environ,
            "HIVE_DSN": hive_dsn(HIVE_CONN_ID),
            "DW_REDSHIFT_DSN": redshift_dsn(DW_CONN_ID),
            "DW_COPY_ROLE": Variable.get(COPY_ROLE_VARIABLE),
        }
        run_pugio(["transfer", "run", str(SPEC), "--start", start, "--end", end], env)

    transfer()


pugio_transfer_hive_orders_daily()
