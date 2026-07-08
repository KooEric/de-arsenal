"""변환 YAML의 Pydantic 모델 — map/steps 선언 (M3 Task 3.1).

하강 경로 (설계 원칙 5): map → steps → SQL(P1 `sql:` step) → Python(P1 UDF).
각 step은 트랜스파일러에서 CTE 하나로 컴파일된다 (docs/02-architecture.md).
증분 변환(P1): `incremental: {mode: by_unit|by_key}` 필드 예약 (docs/07).
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FilterStep(_Frozen):
    filter: str  # SQL WHERE 식 (예: "state = 'open'")


class RenameStep(_Frozen):
    rename: dict[str, str]  # {old: new}


class CastStep(_Frozen):
    cast: dict[str, str]  # {column: type} — DuckDB 타입명


class SelectStep(_Frozen):
    select: list[str]  # 최종 컬럼 목록


class DedupStep(_Frozen):
    dedup: list[str]  # 중복 판정 키 컬럼


class DeriveStep(_Frozen):
    derive: dict[str, str]  # {new_column: SQL 식}


Step = FilterStep | RenameStep | CastStep | SelectStep | DedupStep | DeriveStep


class TransformSpec(_Frozen):
    name: str
    input: Path  # Parquet 디렉터리/파일 (Pugio 산출물 — Arrow/Parquet 허브)
    steps: list[Step]
    output: Path
    # P1 예약: map(필드 매핑표), incremental, sql/python 탈출구
