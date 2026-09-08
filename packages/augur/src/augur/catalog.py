"""스키마 카탈로그 — Parquet 레이크에서 테이블/컬럼/샘플값을 뽑아 검색 단위로 만든다.

RAG의 "R"은 여기서 만든 SchemaDoc을 대상으로 한다. 샘플값을 넣는 이유: LLM이
`status = 'PAID'`처럼 존재하지 않는 리터럴을 지어내는 실패(HALLUCINATED_LITERAL)를
줄이기 위해. 카탈로그는 JSON으로 저장되며 질문마다 재스캔하지 않는다.
"""

import json
import typing as t
from pathlib import Path

import duckdb
from pydantic import BaseModel, ConfigDict

from arsenal_core.errors import FatalError

MAX_SAMPLE_VALUES = 5
SAMPLE_ROW_LIMIT = 10_000
TEXT_TYPES = ("VARCHAR", "TEXT", "STRING")


class ColumnDoc(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    dtype: str
    samples: tuple[str, ...] = ()


class SchemaDoc(BaseModel):
    model_config = ConfigDict(frozen=True)
    table: str
    path: str  # DuckDB에서 FROM에 쓸 경로 (glob 허용)
    row_count: int
    columns: tuple[ColumnDoc, ...]

    def render(self) -> str:
        """프롬프트에 들어가는 형태. 짧고 결정적이어야 캐시·diff가 가능하다."""
        cols = ", ".join(
            f"{c.name} {c.dtype}" + (f" e.g. {list(c.samples)}" if c.samples else "")
            for c in self.columns
        )
        return f"TABLE {self.table} (source: '{self.path}', rows={self.row_count})\n  {cols}"

    def column_names(self) -> frozenset[str]:
        return frozenset(c.name.lower() for c in self.columns)


class Catalog(BaseModel):
    model_config = ConfigDict(frozen=True)
    tables: tuple[SchemaDoc, ...]

    def get(self, table: str) -> SchemaDoc | None:
        return next((d for d in self.tables if d.table == table), None)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Catalog":
        if not path.exists():
            raise FatalError(f"catalog not found: {path} (run `augur index` first)")
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _sample_values(
    con: duckdb.DuckDBPyConnection, path: str, column: str, dtype: str
) -> tuple[str, ...]:
    if not any(tt in dtype.upper() for tt in TEXT_TYPES):
        return ()
    sql = (
        f'SELECT "{column}" AS v, count(*) AS n '
        f"FROM (SELECT * FROM '{path}' LIMIT {SAMPLE_ROW_LIMIT}) "
        f'WHERE "{column}" IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT {MAX_SAMPLE_VALUES}'
    )
    rows = t.cast(list[tuple[t.Any, int]], con.execute(sql).fetchall())
    return tuple(str(r[0]) for r in rows)


def describe(table: str, path: str) -> SchemaDoc:
    """Parquet 경로 하나를 SchemaDoc으로. DuckDB 에러는 FatalError로 변환."""
    con = duckdb.connect()
    try:
        desc = con.execute(f"DESCRIBE SELECT * FROM '{path}'").fetchall()
        count_row = t.cast(tuple[int], con.execute(f"SELECT count(*) FROM '{path}'").fetchone())
        columns = tuple(
            ColumnDoc(
                name=str(r[0]),
                dtype=str(r[1]),
                samples=_sample_values(con, path, str(r[0]), str(r[1])),
            )
            for r in desc
        )
        return SchemaDoc(table=table, path=path, row_count=int(count_row[0]), columns=columns)
    except duckdb.Error as e:
        raise FatalError(f"describe failed for {table} ({path}): {e}") from e
    finally:
        con.close()


def build_catalog(sources: dict[str, str]) -> Catalog:
    """{table_name: parquet_path_or_glob} → Catalog. 테이블명 순으로 정렬해 결정적."""
    docs = tuple(describe(name, sources[name]) for name in sorted(sources))
    return Catalog(tables=docs)
