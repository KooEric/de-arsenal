"""Open a data target read-only in DuckDB and profile it with one set of SQL."""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import duckdb

from arsenal_core.errors import FatalError
from censor.limits import Scale, list_files, measure_files
from censor.reading import Profile
from censor.target import DataTarget, DuckDBTarget, FilesTarget, ScanOptions
from gladius.compile.ident import quote_ident

SAMPLE_SEED: Final = 42
_HASH_BUCKETS: Final = 10_000
_COMPRESSION_SUFFIXES: Final = (".gz", ".zst", ".bz2")
_READERS: Final = {
    ".parquet": "read_parquet({files}, union_by_name = true)",
    ".csv": "read_csv_auto({files}, union_by_name = true)",
    ".tsv": "read_csv_auto({files}, union_by_name = true)",
    ".jsonl": "read_json_auto({files}, union_by_name = true)",
    ".ndjson": "read_json_auto({files}, union_by_name = true)",
    ".json": "read_json_auto({files}, union_by_name = true)",
}
_ATTACHED: Final = "target"


class TargetUnreadable(FatalError):
    """The target was named correctly but cannot be read: its checks are na and the exit is 2."""


class QueryTimeout(Exception):
    def __init__(self, seconds: float) -> None:
        super().__init__(f"timeout {seconds:g}s")
        self.seconds = seconds


@dataclass(frozen=True)
class DataSource:
    con: duckdb.DuckDBPyConnection
    relation: str  # SQL for the rows to profile, after --since and --sample
    columns: tuple[str, ...]
    scale: Scale
    timeout_seconds: float
    key: tuple[str, ...] = ()  # as the data spells the columns
    time_column: str | None = None
    sample: str | None = None  # how the sample was drawn, for the report header
    newest_file_at: datetime | None = None


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def run_query(
    source_or_con: DataSource | duckdb.DuckDBPyConnection, sql: str, timeout: float
) -> list[Any]:
    """Run `sql`; interrupt it after `timeout` seconds and raise `QueryTimeout`."""
    con = source_or_con.con if isinstance(source_or_con, DataSource) else source_or_con
    timer = threading.Timer(timeout, con.interrupt)
    timer.start()
    try:
        return con.execute(sql).fetchall()
    except duckdb.InterruptException as e:
        raise QueryTimeout(timeout) from e
    except duckdb.Error as e:
        if "statement timeout" in str(e):  # Postgres cancelled it on its side
            raise QueryTimeout(timeout) from e
        raise
    finally:
        timer.cancel()


def _connect(scan: ScanOptions, scratch: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    try:
        con.execute("SET TimeZone = 'UTC'")
        con.execute(f"SET memory_limit = {_literal(scan.memory_limit)}")
        con.execute(f"SET temp_directory = {_literal(str(scratch))}")
    except duckdb.Error as e:
        con.close()
        raise FatalError(f"invalid --memory-limit {scan.memory_limit!r}: {e}") from e
    return con


def _reader(paths: list[Path], pattern: str) -> str:
    suffixes: set[str] = set()
    for path in paths:
        name = path.name.lower()
        for extra in _COMPRESSION_SUFFIXES:
            name = name.removesuffix(extra)
        suffixes.add(Path(name).suffix)
    readers = {_READERS.get(s) for s in suffixes}
    if None in readers or len(readers) != 1:
        found = ", ".join(sorted(suffixes)) or "no suffix"
        raise FatalError(
            f"{pattern}: cannot read these files together ({found}); "
            "use one of .parquet, .csv, .tsv, .jsonl, .json"
        )
    template = next(iter(readers))
    assert template is not None
    return template.format(files="[" + ", ".join(_literal(str(p)) for p in paths) + "]")


def _files_relation(target: FilesTarget) -> tuple[str, Scale, datetime]:
    paths = list_files(target.pattern)
    if not paths:
        raise FatalError(f"no file matches {target.pattern}")
    newest = max(p.stat().st_mtime for p in paths)
    return _reader(paths, target.pattern), measure_files(paths), datetime.fromtimestamp(newest, UTC)


def _duckdb_relation(con: duckdb.DuckDBPyConnection, target: DuckDBTarget) -> tuple[str, Scale]:
    if not target.path.is_file():
        raise FatalError(f"no DuckDB file at {target.path}")
    try:
        con.execute(f"ATTACH {_literal(str(target.path))} AS {_ATTACHED} (READ_ONLY)")
    except duckdb.Error as e:
        raise TargetUnreadable(f"could not open {target.path}: {e}") from e
    schema, _, table = target.table.rpartition(".")
    rows = con.execute(
        "SELECT schema_name, estimated_size FROM duckdb_tables() "
        "WHERE database_name = ? AND table_name = ? AND (? = '' OR schema_name = ?) "
        "UNION ALL SELECT schema_name, NULL FROM duckdb_views() "
        "WHERE database_name = ? AND view_name = ? AND (? = '' OR schema_name = ?)",
        [_ATTACHED, table, schema, schema, _ATTACHED, table, schema, schema],
    ).fetchall()
    if not rows:
        raise TargetUnreadable(f"no table {target.table} in {target.path}")
    found_schema, estimated = rows[0]
    relation = f"{_ATTACHED}.{quote_ident(found_schema)}.{quote_ident(table)}"
    if estimated is None:  # a view
        return relation, Scale(unknown=True)
    return relation, Scale(estimated_rows=int(estimated))


def resolve_columns(
    columns: tuple[str, ...], wanted: tuple[str, ...], label: str
) -> tuple[str, ...]:
    """`wanted` spelled as the data spells it (DuckDB matches names without regard to case)."""
    by_lower = {c.lower(): c for c in columns}
    missing = [c for c in wanted if c.lower() not in by_lower]
    if missing:
        raise FatalError(
            f"{label} has no column {', '.join(missing)} (columns: {', '.join(columns)})"
        )
    return tuple(by_lower[c.lower()] for c in wanted)


def narrow(
    relation: str, scan: ScanOptions, key: tuple[str, ...], time_column: str | None
) -> tuple[str, str | None]:
    """Apply --since and --sample to `relation`; returns the SQL and how the sample was drawn."""
    where: list[str] = []
    if scan.since is not None:
        if time_column is None:
            raise FatalError("--since needs --time-column")
        where.append(f"{quote_ident(time_column)} >= TIMESTAMP '{scan.since.isoformat()}'")
    percent = scan.sample_percent
    sample: str | None = None
    suffix = ""
    if percent is not None and key:
        keys = ", ".join(quote_ident(k) for k in key)
        where.append(f"hash({keys}) % {_HASH_BUCKETS} < {round(percent * _HASH_BUCKETS / 100)}")
        sample = f"{percent:g}% of the keys (by key hash)"
    elif percent is not None:
        suffix = f" USING SAMPLE {percent:g} PERCENT (bernoulli, {SAMPLE_SEED})"
        sample = f"{percent:g}% of the rows (bernoulli, seed {SAMPLE_SEED})"
    if not where and not suffix:
        return relation, None
    condition = f" WHERE {' AND '.join(where)}" if where else ""
    return f"(SELECT * FROM {relation}{condition}{suffix})", sample


@contextmanager
def open_source(
    target: DataTarget,
    scan: ScanOptions,
    key: tuple[str, ...],
    time_column: str | None,
    scratch: Path,
) -> Generator[DataSource]:
    """Connect, attach the target read-only and describe it. Nothing is scanned yet."""
    con = _connect(scan, scratch)
    try:
        newest: datetime | None = None
        if isinstance(target, FilesTarget):
            relation, scale, newest = _files_relation(target)
        elif isinstance(target, DuckDBTarget):
            relation, scale = _duckdb_relation(con, target)
        else:
            from censor.postgres import postgres_relation

            relation, scale = postgres_relation(con, target, scan)
        try:
            described = run_query(con, f"DESCRIBE SELECT * FROM {relation}", scan.timeout_seconds)
        except duckdb.Error as e:
            raise TargetUnreadable(f"could not read {target.label}: {e}") from e
        columns = tuple(str(row[0]) for row in described)
        key = resolve_columns(columns, key, target.label)
        if time_column is not None:
            (time_column,) = resolve_columns(columns, (time_column,), target.label)
        if isinstance(target, FilesTarget | DuckDBTarget):
            narrowed, sample = narrow(relation, scan, key, time_column)
        else:
            from censor.postgres import narrow_postgres

            narrowed, sample = narrow_postgres(
                relation, target, scan, key, time_column, is_table=not scale.unknown
            )
        if sample is not None and not key:
            con.execute("SET threads = 1")  # a seeded row sample repeats only on one thread
        yield DataSource(
            con, narrowed, columns, scale, scan.timeout_seconds, key, time_column, sample, newest
        )
    finally:
        con.close()


def _moment(epoch: object) -> datetime | None:
    if not isinstance(epoch, int | float):
        return None
    try:
        return datetime.fromtimestamp(float(epoch), UTC)
    except (ValueError, OverflowError, OSError):  # a year Python cannot represent
        return None


def profile(source: DataSource) -> Profile:
    """Row count, NULLs per column, key NULLs and duplicates, and the period."""
    key, time_column = source.key, source.time_column
    select = ["count(*)"]
    select += [f"count(*) FILTER (WHERE {quote_ident(c)} IS NULL)" for c in source.columns]
    if time_column is not None:
        t = quote_ident(time_column)
        select += [
            f"min({t})::VARCHAR",
            f"max({t})::VARCHAR",
            f"epoch(max(TRY_CAST({t} AS TIMESTAMPTZ)))",
        ]
    any_key_null = " OR ".join(f"{quote_ident(k)} IS NULL" for k in key)
    if key:
        select.append(f"count(*) FILTER (WHERE {any_key_null})")
    timeout = source.timeout_seconds
    row = run_query(source, f"SELECT {', '.join(select)} FROM {source.relation}", timeout)[0]
    n = len(source.columns)
    nulls = {c: int(row[1 + i]) for i, c in enumerate(source.columns)}
    time_min = time_max = None
    time_max_at: datetime | None = None
    if time_column is not None:
        time_min, time_max, epoch = row[1 + n : 4 + n]
        time_max_at = _moment(epoch)
    duplicates: int | None = None
    if key:
        keys = ", ".join(quote_ident(k) for k in key)
        duplicates = int(
            run_query(
                source,
                f"SELECT count(*) FROM (SELECT 1 FROM {source.relation} "
                f"WHERE NOT ({any_key_null}) GROUP BY {keys} HAVING count(*) > 1)",
                timeout,
            )[0][0]
        )
    return Profile(
        rows=int(row[0]),
        null_counts=nulls,
        key_null_rows=int(row[-1]) if key else None,
        duplicate_keys=duplicates,
        time_min=time_min,
        time_max=time_max,
        time_max_at=time_max_at,
    )
