"""변환 YAML의 Pydantic 모델 — map/steps 선언 (M3 Task 3.1).

하강 경로 (설계 원칙 5): map → steps → SQL(`sql:` step) → Python(`python:` UDF).
각 step은 트랜스파일러에서 CTE 하나로 컴파일된다 (docs/02-architecture.md).
증분 변환(P1): `incremental: {mode: by_unit|by_key}`로 신규 입력만 재계산한다 (docs/07).
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _reject_statement_separator(value: str, *, slot: str) -> str:
    """표현식 안의 ';'를 거부한다 — 내용 검증이 아니라 구조 방어.

    표현식은 `COPY (<컴파일된 SQL>) TO ...` 템플릿 안에 삽입되므로, ';'가 허용되면
    래퍼를 닫고 임의의 두 번째 문(예: 다른 경로로 COPY)을 실행할 수 있다. 표현식
    *내용*은 여전히 사용자 소유의 SQL이고 검증 대상이 아니다 (compile/ident.py의
    신뢰 모델). sql 스텝은 처음부터 이 검사를 하고 있었다 — 나머지 슬롯에도 같은
    규칙을 적용해 일관성을 맞춘다.
    """
    if ";" in value:
        raise ValueError(f"{slot} expression must not contain ';' (statement separator)")
    return value


class FilterStep(_Frozen):
    filter: str  # SQL WHERE 식 (예: "state = 'open'")

    @field_validator("filter")
    @classmethod
    def _no_statement_separator(cls, value: str) -> str:
        return _reject_statement_separator(value, slot="filter")


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

    @field_validator("derive")
    @classmethod
    def _no_statement_separator(cls, value: dict[str, str]) -> dict[str, str]:
        for expr in value.values():
            _reject_statement_separator(expr, slot="derive")
        return value


class SqlStep(_Frozen):
    """Raw SQL escape hatch; ``{input}`` is replaced with the previous CTE."""

    sql: str

    @field_validator("sql")
    @classmethod
    def _must_reference_input(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("sql step must not be empty")
        if value.count("{input}") != 1:
            raise ValueError("sql step must contain exactly one {input} placeholder")
        if ";" in value:
            raise ValueError("sql step must contain one SELECT expression without ';'")
        return value


class PythonUdfStep(_Frozen):
    """Register a user function and append its result as a new column."""

    python: str
    args: list[str] = Field(min_length=1)
    output: str
    arg_types: list[str] | None = None
    return_type: str = "VARCHAR"

    @field_validator("python")
    @classmethod
    def _target_is_importable_shape(cls, value: str) -> str:
        if value.count(":") != 1:
            raise ValueError("python UDF target must use module:function form")
        module, function = value.split(":")
        if not module or not function:
            raise ValueError("python UDF target must use module:function form")
        return value

    @model_validator(mode="after")
    def _validate_types(self) -> "PythonUdfStep":
        if self.arg_types is not None and len(self.arg_types) != len(self.args):
            raise ValueError("python UDF arg_types must match args length")
        if not self.output:
            raise ValueError("python UDF output must not be empty")
        return self


Step = (
    FilterStep
    | RenameStep
    | CastStep
    | SelectStep
    | DedupStep
    | DeriveStep
    | SqlStep
    | PythonUdfStep
)


class IncrementalSpec(_Frozen):
    """Incremental execution mode and optional upsert key."""

    mode: Literal["by_unit", "by_key"]
    key: list[str] | None = Field(
        default=None,
        description="Columns used to keep the newest transformed row in by_key mode.",
    )

    @model_validator(mode="after")
    def _key_required_for_by_key(self) -> "IncrementalSpec":
        if self.mode == "by_key" and not self.key:
            raise ValueError("incremental.by_key requires a non-empty key")
        return self


class TransformSpec(_Frozen):
    name: str
    # Parquet 디렉터리/파일 경로 (Pugio 산출물 — Arrow/Parquet 허브).
    # str로 보관 — Path로 파싱하면 "./in" 같은 상대경로의 선행 "./"가 정규화로
    # 사라져 컴파일된 SQL 리터럴이 원본 스펙과 달라진다(트랜스파일러 투명성 원칙 위반).
    input: str
    map: dict[str, str] | None = None  # {new_column: source_expr} — steps보다 먼저 적용
    steps: list[Step] = []
    incremental: IncrementalSpec | None = None
    state_dir: str = ".gladius"
    # 출력 디렉터리. str로 보관 — Path로 파싱하면 "s3://bucket/x" 같은 URI 스킴이
    # "s3:/bucket/x"로 정규화되어 훼손된다 (P1 httpfs/S3 싱크가 이 필드를 그대로
    # 쓴다). 엔진이 mkdir/os.replace/rmtree 같은 파일시스템 연산이 필요한 지점에서
    # Path(...)로 감싸 해석한다.
    output: str

    @field_validator("input", "output", "state_dir", mode="before")
    @classmethod
    def _coerce_path_fields_to_str(cls, v: object) -> object:
        if isinstance(v, Path):
            return str(v)
        return v

    @model_validator(mode="after")
    def _require_map_or_steps(self) -> "TransformSpec":
        if not self.map and not self.steps:
            raise ValueError("transform spec requires at least one of 'map' or 'steps'")
        return self

    @field_validator("name")
    @classmethod
    def _name_is_path_safe(cls, value: str) -> str:
        if "/" in value or "\\" in value or ".." in value:
            raise ValueError("transform name must not contain '/', '\\\\', or '..'")
        return value

    @field_validator("map")
    @classmethod
    def _map_has_no_statement_separator(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        for expr in (value or {}).values():
            _reject_statement_separator(expr, slot="map")
        return value
