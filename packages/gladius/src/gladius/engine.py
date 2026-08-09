"""DuckDB 실행 엔진 (M3 Task 3.3~3.6). 엔진은 빌린다 — 우리는 컴파일과 신뢰성만.

Parquet in → (compile_sql 결과 실행) → Parquet out. Arrow 경유 zero-copy.
query()는 즉석 SQL(미니 DWH) — 원클릭 경험의 "아하 모먼트".
"""

import hashlib
import os
import shutil
import typing as t
from pathlib import Path

import duckdb
import pyarrow as pa

from arsenal_core.errors import FatalError
from gladius.compile import compile_sql
from gladius.compile.ident import quote_str_literal
from gladius.incremental import (
    IncrementalStore,
    InputFile,
    atomic_replace_directory,
    discover_input_files,
    reset_output,
    state_path,
    transform_hash,
)
from gladius.spec import TransformSpec
from gladius.udf import register_python_udfs


def run_transform(spec: TransformSpec) -> Path:
    """변환을 실행하고 output 경로를 반환한다.

    전체 재계산 의미론(P0): 매 실행은 output을 통째로 다시 쓴다. 임시 디렉터리에
    쓰고 os.replace로 원자 교체 — 실패한 실행이 이전 성공 출력을 훼손하지 않는다.
    """
    if spec.incremental is not None:
        return _run_incremental(spec)
    sql = compile_sql(spec)
    con = duckdb.connect()  # in-memory, 상태 없음
    register_python_udfs(con, spec)
    con.execute(f"PRAGMA threads={os.cpu_count() or 1}")
    # spec.output은 str(URI 스킴 보존용) — 파일시스템 연산이 필요한 여기서만 Path로 감싼다.
    output = Path(spec.output)
    tmp = output.with_name(output.name + ".tmp")
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        out_path = quote_str_literal(str(tmp / "part-0.parquet"))
        con.execute(f"COPY ({sql}) TO {out_path} (FORMAT PARQUET, COMPRESSION ZSTD)")
    except duckdb.Error as e:
        raise FatalError(f"transform failed: {e}\n--- compiled SQL ---\n{sql}") from e
    finally:
        con.close()
    if output.exists():
        shutil.rmtree(output)  # 변환 출력은 전체 재계산 의미론 (P0)
    os.replace(tmp, output)
    return output


def _run_incremental(spec: TransformSpec) -> Path:
    incremental = spec.incremental
    if incremental is None:  # pragma: no cover - guarded by run_transform
        raise FatalError("incremental execution requires an incremental spec")
    files = discover_input_files(spec)
    output = Path(spec.output)
    store = IncrementalStore(state_path(spec))
    try:
        current_hash = transform_hash(spec)
        known = store.signatures()
        current = {item.key: item.signature for item in files}
        changed_existing = any(
            key in known and known[key] != signature for key, signature in current.items()
        )
        removed_input = any(key not in current for key in known)
        if (
            store.spec_hash() != current_hash
            or (not output.exists() and known)
            or changed_existing
            or removed_input
        ):
            reset_output(output)
            store.reset(current_hash)
            known = {}
        pending = [item for item in files if known.get(item.key) != item.signature]
        if not pending:
            output.mkdir(parents=True, exist_ok=True)
            return output
        if not files:
            output.mkdir(parents=True, exist_ok=True)
            return output

        sql = compile_sql(spec, [item.path for item in pending])
        con = duckdb.connect()
        register_python_udfs(con, spec)
        con.execute(f"PRAGMA threads={os.cpu_count() or 1}")
        tmp_input = output.with_name(output.name + ".input.tmp")
        tmp_output = output.with_name(output.name + ".tmp")
        try:
            if tmp_input.exists():
                shutil.rmtree(tmp_input)
            tmp_input.mkdir(parents=True)
            new_part = tmp_input / "new.parquet"
            con.execute(
                f"COPY ({sql}) TO {quote_str_literal(str(new_part))} "
                "(FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            if incremental.mode == "by_unit":
                _append_incremental_part(output, new_part, pending)
            else:
                _merge_incremental_output(con, output, new_part, tmp_output, incremental.key or [])
            store.mark_processed(pending)
        except duckdb.Error as e:
            raise FatalError(
                f"incremental transform failed: {e}\n--- compiled SQL ---\n{sql}"
            ) from e
        finally:
            con.close()
            if tmp_input.exists():
                shutil.rmtree(tmp_input)
            if tmp_output.exists():
                shutil.rmtree(tmp_output)
        return output
    finally:
        store.close()


def _append_incremental_part(output: Path, new_part: Path, pending: list[InputFile]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    batch_id = hashlib.sha256(
        "|".join(f"{item.key}:{item.signature}" for item in pending).encode()
    ).hexdigest()[:16]
    target = output / f"part-{batch_id}.parquet"
    os.replace(new_part, target)


def _merge_incremental_output(
    con: duckdb.DuckDBPyConnection,
    output: Path,
    new_part: Path,
    tmp_output: Path,
    keys: list[str],
) -> None:
    new_relation = quote_str_literal(str(new_part))
    if output.exists() and any(output.glob("**/*.parquet")):
        old_relation = quote_str_literal(str(output / "**/*.parquet"))
        partition = ", ".join('"' + key.replace('"', '""') + '"' for key in keys)
        query = (
            "SELECT * EXCLUDE (__gladius_precedence) FROM ("
            f"SELECT *, 0 AS __gladius_precedence FROM "
            f"read_parquet({new_relation}, union_by_name=true) "
            "UNION ALL "
            f"SELECT *, 1 AS __gladius_precedence FROM "
            f"read_parquet({old_relation}, union_by_name=true)"
            f") QUALIFY row_number() OVER (PARTITION BY {partition} "
            "ORDER BY __gladius_precedence) = 1"
        )
    else:
        query = f"SELECT * FROM read_parquet({new_relation}, union_by_name=true)"
    tmp_output.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"COPY ({query}) TO {quote_str_literal(str(tmp_output / 'part-0.parquet'))} "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    atomic_replace_directory(tmp_output, output)


def query(sql: str) -> pa.Table:
    """Parquet 레이크에 즉석 SQL — FROM './data/*.parquet' 경로 직접 참조.

    in-memory DuckDB, 상태 없음. DuckDB 에러는 FatalError로 변환.
    """
    con = duckdb.connect()  # in-memory, 상태 없음
    try:
        arrow_table = con.sql(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            sql
        ).to_arrow_table()
        return t.cast(pa.Table, arrow_table)
    except duckdb.Error as e:
        raise FatalError(str(e)) from e
    finally:
        con.close()
