"""Preview and publish a folder-backed dataset with one atomic DuckDB transaction."""

import glob
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import typer

from arsenal.dataset_postgres import parse_target, publish
from arsenal_core.errors import FatalError
from gladius.compile.ident import quote_ident

app = typer.Typer(help="파일 데이터셋의 변경을 검토하고 게시합니다.")
STORE = Path(".arsenal/dataset.duckdb")
EXCEL_SUFFIXES = frozenset({".xlsx", ".xlsm", ".xlsb", ".xls"})
# Excel keeps `~$name.xlsx` beside a workbook while someone has it open. It is not data.
OFFICE_LOCK_PREFIX = "~$"


@dataclass
class Rules:
    files: str
    distinct: bool = False
    key: str | None = None
    time_column: str | None = None
    measure: str | None = None
    max_age_hours: float | None = None
    postgres_table: str | None = None
    postgres_dsn_env: str | None = None


def fingerprints(pattern: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in sorted(glob.glob(pattern, recursive=True)):
        path = Path(name)
        if path.name.startswith(OFFICE_LOCK_PREFIX) and path.suffix.lower() in EXCEL_SUFFIXES:
            continue
        if path.is_file():
            with path.open("rb") as stream:
                result[path.as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    if not result:
        raise FatalError(f"입력 파일이 없습니다. 파일 도착 여부와 경로를 확인하세요: {pattern}")
    return result


def metadata(db: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    if not db.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name='runs'").fetchall()[0][
        0
    ]:
        return {}
    return json.loads(
        db.execute("SELECT report FROM runs ORDER BY id DESC LIMIT 1").fetchall()[0][0]
    )


def column_types(db: duckdb.DuckDBPyConnection, table: str) -> dict[str, str]:
    return {row[0]: row[1] for row in db.execute(f"DESCRIBE {table}").fetchall()}


def align(db: duckdb.DuckDBPyConnection, left: str, right: str) -> bool:
    """Give two temporary tables the same column types where one side holds no values.

    A reader that types a column from its values (JSON) gives a column that is NULL in
    every row a placeholder type. Such a column takes the other side's type, preferring
    ``left``. A column with values on both sides and different types is a real schema
    change: nothing is done for it and the result is False.
    """
    a, b = column_types(db, left), column_types(db, right)
    if a.keys() != b.keys():
        return False
    for name in a:
        if a[name] == b[name]:
            continue
        col = quote_ident(name)
        if not db.execute(f"SELECT count({col}) FROM {right}").fetchall()[0][0]:
            db.execute(f"ALTER TABLE {right} ALTER {col} TYPE {a[name]}")
        elif not db.execute(f"SELECT count({col}) FROM {left}").fetchall()[0][0]:
            db.execute(f"ALTER TABLE {left} ALTER {col} TYPE {b[name]}")
        else:
            return False
    return True


def load_excel(db: duckdb.DuckDBPyConnection, path: str, table: str) -> None:
    """Read a workbook's first sheet as text into ``table``, as CSV is read."""
    try:
        import fastexcel  # pyright: ignore[reportMissingImports]
    except ImportError as e:
        raise FatalError(
            "Excel 파일을 읽으려면 excel 추가 기능을 설치하세요: "
            'uv tool install "de-arsenal[excel]"'
        ) from e
    try:
        sheet = fastexcel.read_excel(path).load_sheet(0, dtypes="string")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        batch = sheet.to_arrow()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    except Exception as e:  # fastexcel raises its own error types per failure
        raise FatalError(f"Excel 파일을 읽을 수 없습니다. 파일을 확인하세요: {path} ({e})") from e
    db.register("excel_sheet", batch)
    try:
        db.execute(f"CREATE TEMP TABLE {table} AS SELECT * FROM excel_sheet")
    finally:
        db.unregister("excel_sheet")


def load_input(db: duckdb.DuckDBPyConnection, path: str, reader: str | None, table: str) -> None:
    if reader is None:
        load_excel(db, path, table)
    else:
        db.execute(f"CREATE TEMP TABLE {table} AS SELECT * FROM {reader}", [path])


def input_reader(paths: list[str]) -> str | None:
    """The DuckDB reader for one snapshot's files, or None for Excel workbooks."""
    suffixes = {Path(p).suffix.lower() for p in paths}
    if suffixes == {".csv"}:
        return "read_csv(?, header=true, all_varchar=true)"
    if suffixes <= {".jsonl", ".ndjson"}:
        return "read_json_auto(?, format='newline_delimited')"
    if suffixes == {".parquet"}:
        return "read_parquet(?)"
    if suffixes <= EXCEL_SUFFIXES:
        return None
    raise FatalError("한 작업에는 같은 형식의 CSV, JSONL, Parquet 또는 Excel 파일을 지정하세요.")


def read_inputs(db: duckdb.DuckDBPyConnection, paths: list[str]) -> None:
    reader = input_reader(paths)
    # Read individually: a reordered header must never shift values into another column.
    for index, path in enumerate(paths):
        if index == 0:
            load_input(db, path, reader, "raw")
            continue
        load_input(db, path, reader, "part")
        if not align(db, "raw", "part"):
            raise FatalError(f"파일 간 열 또는 자료형이 다릅니다. 열 구성을 확인하세요: {path}")
        db.execute("INSERT INTO raw BY NAME SELECT * FROM part")
        db.execute("DROP TABLE part")


def profile(db: duckdb.DuckDBPyConnection, table: str, rules: Rules) -> dict[str, Any]:
    fields = ["count(*) AS rows"]
    if rules.measure:
        col = quote_ident(rules.measure)
        fields.extend(
            [f"sum(CAST({col} AS DOUBLE)) AS total", f"count(*) - count({col}) AS missing_measure"]
        )
    if rules.time_column:
        col = quote_ident(rules.time_column)
        fields.extend(
            [
                f"count(*) - count({col}) AS missing_time",
                f"min(CAST({col} AS TIMESTAMPTZ))::VARCHAR AS first",
                f"max(CAST({col} AS TIMESTAMPTZ))::VARCHAR AS last",
            ]
        )
    cursor = db.execute(f"SELECT {', '.join(fields)} FROM {table}")
    return dict(zip([d[0] for d in cursor.description], cursor.fetchall()[0], strict=True))


def diff(db: duckdb.DuckDBPyConnection, query: str) -> dict[str, Any]:
    count = db.execute(f"SELECT count(*) FROM ({query})").fetchall()[0][0]
    examples = db.execute(
        f"SELECT to_json(struct_pack(*COLUMNS(*))) FROM ({query}) sample LIMIT 3"
    ).fetchall()
    return {"count": count, "examples": [json.loads(row[0]) for row in examples]}


def compare(
    db: duckdb.DuckDBPyConnection, rules: Rules, previous: dict[str, Any]
) -> dict[str, Any]:
    if not previous:
        db.execute("CREATE TEMP TABLE published AS FROM candidate LIMIT 0")
    base = "published"
    if column_types(db, "published") != column_types(db, "candidate"):
        # Compare against a copy: the published table is read-only during a preview.
        db.execute("CREATE TEMP TABLE baseline AS FROM published")
        if not align(db, "baseline", "candidate"):
            raise FatalError("게시된 결과와 열 또는 자료형이 다릅니다. 입력 스키마를 확인하세요.")
        base = "baseline"
    schema = db.execute(f"DESCRIBE {base}").fetchall()
    columns = ", ".join(quote_ident(row[0]) for row in schema)
    if not rules.key:
        return {
            label: diff(db, f"SELECT {columns} FROM {a} EXCEPT ALL SELECT {columns} FROM {b}")
            for label, a, b in (
                ("added", "candidate", base),
                ("removed", base, "candidate"),
            )
        }
    key = quote_ident(rules.key)
    for label, table in (("candidate", "candidate"), ("published", base)):
        invalid = db.execute(
            f"SELECT {key} FROM {table} GROUP BY {key} HAVING {key} IS NULL OR count(*) > 1 LIMIT 1"
        ).fetchone()
        if invalid:
            raise FatalError(
                f"{rules.key} 식별값이 비었거나 중복됩니다 ({label}). 예: {invalid[0]!r}"
            )
    return {
        "added": diff(db, f"FROM candidate ANTI JOIN {base} USING ({key})"),
        "removed": diff(db, f"FROM {base} ANTI JOIN candidate USING ({key})"),
        "updated": diff(
            db,
            "SELECT struct_pack(*COLUMNS(p.*)) AS before, struct_pack(*COLUMNS(c.*)) AS after "
            f"FROM {base} p JOIN candidate c "
            f"USING ({key}) WHERE "
            + " OR ".join(
                f"p.{quote_ident(r[0])} IS DISTINCT FROM c.{quote_ident(r[0])}" for r in schema
            ),
        ),
    }


def assess(db: duckdb.DuckDBPyConnection, rules: Rules, previous: dict[str, Any]) -> dict[str, Any]:
    files = fingerprints(rules.files)
    old = previous.get("fingerprints", {})
    read_inputs(db, list(files))
    if files != fingerprints(rules.files):
        raise FatalError("검토 중 입력이 변경됐습니다. 다시 실행하세요.")
    db.execute(
        "CREATE TEMP TABLE candidate AS SELECT "
        + ("DISTINCT " if rules.distinct else "")
        + "* FROM raw"
    )
    result = profile(db, "candidate", rules)
    prior = previous.get("result", {})
    delta = {"rows": result["rows"] - prior.get("rows", 0)}
    if (
        rules.measure
        and previous.get("rules", {}).get("measure") == rules.measure
        and prior.get("total") is not None
        and result["total"] is not None
    ):
        delta["total"] = result["total"] - prior["total"]
    return {
        "rules": asdict(rules),
        "fingerprints": files,
        "files": {
            path: (
                "removed"
                if path not in files
                else "new"
                if path not in old
                else "unchanged"
                if files[path] == old[path]
                else "revised"
            )
            for path in sorted(files.keys() | old.keys())
        },
        "source": profile(db, "raw", rules),
        "result": result,
        "change_since_publication": delta,
        "previous_result": previous.get("result"),
        "excluded": diff(db, "FROM raw EXCEPT ALL FROM candidate"),
        "changes": compare(db, rules, previous),
    }


def execute(
    apply: bool,
    files: str | None,
    distinct: bool | None,
    key: str | None,
    time_column: str | None,
    measure: str | None,
    max_age_hours: float | None,
    expect: str | None = None,
    postgres_table: str | None = None,
    postgres_dsn_env: str | None = None,
) -> dict[str, Any]:
    if apply:
        STORE.parent.mkdir(parents=True, exist_ok=True)
    target = str(STORE) if apply or STORE.exists() else ":memory:"
    with duckdb.connect(target, read_only=not apply and STORE.exists()) as db:
        db.execute("SET TimeZone='UTC'")
        db.execute("BEGIN")
        try:
            previous = metadata(db)
            values = dict(previous.get("rules", {}))
            values.update(
                {
                    k: v
                    for k, v in dict(
                        files=files,
                        distinct=distinct,
                        key=key,
                        time_column=time_column,
                        measure=measure,
                        max_age_hours=max_age_hours,
                        postgres_table=postgres_table,
                        postgres_dsn_env=postgres_dsn_env,
                    ).items()
                    if v is not None
                }
            )
            if not values.get("files"):
                raise FatalError("첫 실행에는 --files 'input/*.csv'처럼 입력을 지정하세요.")
            rules = Rules(**values)
            target = parse_target(rules.postgres_table, rules.postgres_dsn_env)
            report = assess(db, rules, previous)
            if target is not None:
                report["publish"] = {"postgres": target.name}
            token_inputs = [previous.get("version"), report["rules"], report["fingerprints"]]
            report["version"] = hashlib.sha256(
                json.dumps(token_inputs, sort_keys=True).encode()
            ).hexdigest()
            if expect is not None and report["version"] != expect:
                raise FatalError(
                    "미리보기 이후 입력 또는 게시 버전이 달라졌습니다. 다시 검토하세요."
                )
            if apply:
                now = datetime.now(UTC).isoformat()
                report["last_success"] = now
                report["last_input_change"] = (
                    previous.get("last_input_change")
                    if previous.get("fingerprints") == report["fingerprints"]
                    else now
                )
                if not previous:
                    db.execute("DROP TABLE published")  # empty temporary comparison baseline
                db.execute("CREATE OR REPLACE TABLE published AS FROM candidate")
                if target is not None:
                    # Postgres commits first. If it fails, the DuckDB publication rolls back
                    # too, so both keep the previous snapshot.
                    report["published_to"] = {"postgres": publish(db, "published", target)}
                db.execute("CREATE TABLE IF NOT EXISTS runs (id BIGINT, report VARCHAR)")
                db.execute(
                    "INSERT INTO runs SELECT coalesce(max(id),0)+1, ? FROM runs",
                    [json.dumps(report, default=str)],
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS failure (message VARCHAR, failed_at TIMESTAMPTZ)"
                )
                db.execute("DELETE FROM failure")
            db.execute("COMMIT")
            return report
        except Exception as exc:
            db.execute("ROLLBACK")
            if apply:
                db.execute("BEGIN")
                db.execute(
                    "CREATE OR REPLACE TABLE failure AS SELECT ? AS message, now() AS failed_at",
                    [str(exc)],
                )
                db.execute("COMMIT")
            raise


def status_report(path: Path = STORE) -> dict[str, Any]:
    if not path.exists():
        return {"state": "never_run", "recovery": "arsenal dataset preview --files 'input/*.csv'"}
    with duckdb.connect(str(path), read_only=True) as db:
        last = metadata(db)
        failure = None
        if db.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name='failure'").fetchall()[
            0
        ][0]:
            failure = db.execute("SELECT message, failed_at::VARCHAR FROM failure").fetchone()
    limit = last.get("rules", {}).get("max_age_hours")
    stale = limit is not None and datetime.now(UTC) - datetime.fromisoformat(
        last["last_input_change"]
    ) > timedelta(hours=limit)
    return {
        "state": "failed"
        if failure
        else "stale"
        if stale
        else "published"
        if last
        else "never_run",
        "last_success": last.get("last_success"),
        "last_input_change": last.get("last_input_change"),
        "result": last.get("result"),
        "rules": last.get("rules"),
        "error": failure[0] if failure else None,
        "failed_at": failure[1] if failure else None,
        "recovery": "입력을 확인하고 arsenal dataset preview 후 arsenal dataset apply"
        if failure or stale
        else None,
    }


def emit(report: dict[str, Any]) -> None:
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2, default=str))


def handle(apply: bool, **opts: Any) -> None:
    try:
        emit(execute(apply, **opts))
    except (FatalError, OSError, duckdb.Error) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command()
def preview(
    files: str | None = None,
    distinct: bool | None = None,
    key: str | None = None,
    time_column: str | None = None,
    measure: str | None = None,
    max_age_hours: float | None = typer.Option(None, min=0),
    postgres_table: str | None = typer.Option(
        None, help="함께 게시할 Postgres 테이블 (schema.table)"
    ),
    postgres_dsn_env: str | None = typer.Option(
        None, help="Postgres 접속 문자열이 든 환경변수 이름"
    ),
) -> None:
    """원본과 게시 예정 결과, 마지막 게시 이후 차이를 확인합니다."""
    handle(
        False,
        files=files,
        distinct=distinct,
        key=key,
        time_column=time_column,
        measure=measure,
        max_age_hours=max_age_hours,
        postgres_table=postgres_table,
        postgres_dsn_env=postgres_dsn_env,
    )


@app.command()
def apply(
    files: str | None = None,
    distinct: bool | None = None,
    key: str | None = None,
    time_column: str | None = None,
    measure: str | None = None,
    max_age_hours: float | None = typer.Option(None, min=0),
    expect: str | None = None,
    postgres_table: str | None = typer.Option(
        None, help="함께 게시할 Postgres 테이블 (schema.table)"
    ),
    postgres_dsn_env: str | None = typer.Option(
        None, help="Postgres 접속 문자열이 든 환경변수 이름"
    ),
) -> None:
    """현재 파일 집합을 게시합니다. --expect로 미리 본 버전을 지정할 수 있습니다."""
    handle(
        True,
        files=files,
        distinct=distinct,
        key=key,
        time_column=time_column,
        measure=measure,
        max_age_hours=max_age_hours,
        expect=expect,
        postgres_table=postgres_table,
        postgres_dsn_env=postgres_dsn_env,
    )


@app.command()
def status() -> None:
    """마지막 성공, 데이터 기간, 입력 지연과 실패 복구 방법을 확인합니다."""
    try:
        report = status_report()
    except duckdb.Error as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    emit(report)
    if report["state"] != "published":
        raise typer.Exit(1)
