"""변환 YAML의 Pydantic 모델 — map/steps 선언 (M3 Task 3.1).

하강 경로 (설계 원칙 5): map → steps → SQL(P1 `sql:` step) → Python(P1 UDF).
각 step은 트랜스파일러에서 CTE 하나로 컴파일된다 (docs/02-architecture.md).
증분 변환(P1): `incremental: {mode: by_unit|by_key}` 필드 예약 (docs/07).
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


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
    # Parquet 디렉터리/파일 경로 (Pugio 산출물 — Arrow/Parquet 허브).
    # str로 보관 — Path로 파싱하면 "./in" 같은 상대경로의 선행 "./"가 정규화로
    # 사라져 컴파일된 SQL 리터럴이 원본 스펙과 달라진다(트랜스파일러 투명성 원칙 위반).
    input: str
    map: dict[str, str] | None = None  # {new_column: source_expr} — steps보다 먼저 적용
    steps: list[Step] = []
    output: Path
    # P1 예약: incremental, sql/python 탈출구

    @field_validator("input", mode="before")
    @classmethod
    def _coerce_input_to_str(cls, v: object) -> object:
        if isinstance(v, Path):
            return str(v)
        return v

    @model_validator(mode="after")
    def _require_map_or_steps(self) -> "TransformSpec":
        if not self.map and not self.steps:
            raise ValueError("transform spec requires at least one of 'map' or 'steps'")
        return self
