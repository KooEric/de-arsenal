"""파이프라인 YAML의 Pydantic 모델. 선언이 인터페이스다 — extra는 거부.

YAML 전체 형태: docs/02-architecture.md "파이프라인 YAML 스펙"
SourceSpec은 discriminated union(rest/file/database/python)이다. 기존 이름
`SourceSpec`은 union 별칭으로 유지해 M1 코드의 import가 깨지지 않게 한다 — 단,
`SourceSpec(type="rest", ...)` 같은 직접 생성자 호출은 더 이상 불가하다
(Annotated[Union[...], ...]는 호출 불가). REST 소스를 직접 만들 때는
`RestSourceSpec`을 사용한다 (M1의 REST 필드를 그대로 옮긴 것 — 필드 호환).
필드 확장 계획: M2에서 pagination.mode(page/cursor), sink.type(duckdb/postgres),
auth, validate 블록 추가. P1 필드는 이름을 미리 예약해 하위 호환을 지킨다.
"""

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PaginationSpec(_Frozen):
    mode: Literal["offset", "page", "cursor", "link"]
    param: str = "offset"
    size_param: str = "limit"
    size: int = 100
    start_page: int = 1  # "page" 모드의 시작 페이지 번호
    cursor_param: str | None = None  # "cursor" 모드: 커서를 실어 보낼 요청 파라미터명
    cursor_path: str | None = None  # "cursor" 모드: 응답에서 다음 커서를 읽을 dot-path
    record_path: str | None = None  # envelope 내 레코드 배열의 dot-path (None=응답 자체가 배열)

    @model_validator(mode="after")
    def _cursor_fields_required(self) -> "PaginationSpec":
        if self.mode == "cursor" and not (self.cursor_param and self.cursor_path):
            raise ValueError("cursor mode requires cursor_param and cursor_path")
        return self


class RateLimitSpec(_Frozen):
    rps: float = Field(gt=0)  # M2: 토큰 버킷 + 429 적응 감속. 0/음수는 TokenBucket의
    # 1.0/rps 계산에서 ZeroDivisionError/역방향 스로틀로 이어져 여기서 차단한다.


class AuthSpec(_Frozen):
    """REST 소스 인증 (M2). static_token은 헤더에 그대로, oauth2는 client_credentials
    흐름으로 토큰을 획득/갱신한다 (구현은 M2-D 이후, 여기는 모델만)."""

    type: Literal["static_token", "oauth2_client_credentials"]
    token_env: str | None = None
    token_url: str | None = None
    client_id_env: str | None = None
    client_secret_env: str | None = None
    expiry_buffer_s: int = 60

    @model_validator(mode="after")
    def _required_fields_for_type(self) -> "AuthSpec":
        if self.type == "static_token" and not self.token_env:
            raise ValueError("static_token auth requires token_env")
        if self.type == "oauth2_client_credentials":
            missing = [
                name
                for name, value in (
                    ("token_url", self.token_url),
                    ("client_id_env", self.client_id_env),
                    ("client_secret_env", self.client_secret_env),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"oauth2_client_credentials auth requires {', '.join(missing)}")
        return self


class RestSourceSpec(_Frozen):
    """REST API 소스 — M1 SourceSpec의 필드를 그대로 옮긴 것 (하위 호환)."""

    type: Literal["rest"]
    url: str
    headers: dict[str, str] = {}
    pagination: PaginationSpec
    rate_limit: RateLimitSpec | None = None
    encoding: str = "utf-8"  # M2: euc-kr 등 비UTF-8 처리
    auth: AuthSpec | None = None


class FileSourceSpec(_Frozen):
    """로컬 파일 소스 (M2 Task 2.11) — csv/jsonl/excel to Arrow."""

    type: Literal["file"]
    path: str  # 글롭 (예: "./raw/**/*.csv")
    format: Literal["auto", "csv", "jsonl", "excel"] = "auto"
    encoding: str = "utf-8"


class SplitSpec(_Frozen):
    key: str  # 단조 증가 키 (PK/serial/타임스탬프)
    chunk: int = Field(default=100_000, gt=0)  # 0/음수는 무한 루프로 이어져 검증 단계에서 차단


class DatabaseSourceSpec(_Frozen):
    """운영 DB 소스 (M2 Task 2.12).

    초안(draft): dialect="sqlite"만 구현체가 지원 — Python 표준 sqlite3 드라이버.
    postgres/mysql(DuckDB scanner 차용, docs/09 수 1)은 P1로 이연.
    """

    type: Literal["database"]
    dialect: Literal["postgres", "mysql", "sqlite"]
    dsn_env: str  # DSN은 환경변수로만 (비밀 원칙)
    table: str
    split: SplitSpec


class PythonSourceSpec(_Frozen):
    """커스텀 Python 소스 탈출구 (M2 Task 2.13). P1 dlt 래퍼의 기반 메커니즘."""

    type: Literal["python"]
    target: str  # "pkg.module:ClassName" — Source 프로토콜 구현체
    options: dict[str, Any] = {}  # 생성자 첫 인자로 전달


SourceSpec = Annotated[
    RestSourceSpec | FileSourceSpec | DatabaseSourceSpec | PythonSourceSpec,
    Field(discriminator="type"),
]


class ParquetSinkSpec(_Frozen):
    type: Literal["parquet"]
    # str로 보관 — Path로 파싱하면 "s3://bucket/x" 같은 URI 스킴이 "s3:/bucket/x"로
    # 정규화되어 훼손된다 (P1 httpfs/S3 싱크가 이 필드를 그대로 쓴다). sink/엔진이
    # 파일시스템 연산이 필요한 지점에서 Path(...)로 감싸 해석한다.
    path: str

    @field_validator("path", mode="before")
    @classmethod
    def _coerce_path_to_str(cls, v: object) -> object:
        if isinstance(v, Path):
            return str(v)
        return v


class DuckDBSinkSpec(_Frozen):
    """DuckDB 파일 싱크 (M2) — temp 테이블 → MERGE로 멱등 upsert."""

    type: Literal["duckdb"]
    path: str
    table: str
    merge_key: list[str]


class PostgresSinkSpec(_Frozen):
    """Postgres 싱크 (M2) — merge_key 기준 upsert."""

    type: Literal["postgres"]
    dsn_env: str
    table: str
    merge_key: list[str]


SinkSpec = Annotated[
    ParquetSinkSpec | DuckDBSinkSpec | PostgresSinkSpec,
    Field(discriminator="type"),
]


class ValidateRule(_Frozen):
    field: str
    not_null: bool = False
    unique: bool = False
    min: float | None = None
    max: float | None = None


class ValidateSpec(_Frozen):
    rules: list[ValidateRule]
    on_violation: Literal["block", "quarantine", "warn"] = "quarantine"


class PipelineSpec(_Frozen):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    name: str
    state_dir: Path = Path(".arsenal")
    source: SourceSpec
    sink: SinkSpec
    validation: ValidateSpec | None = Field(default=None, alias="validate")

    @field_validator("name")
    @classmethod
    def _name_is_path_safe(cls, v: str) -> str:
        """name은 `state_dir/{name}.db`와 `dlq/{name}/`에 그대로 꽂힌다 (M2-E FIX 8) —
        경로 구분자나 `..`가 섞이면 상태 DB/DLQ 경로를 다른 디렉터리로 탈출시킬 수
        있어 여기서 차단한다."""
        if "/" in v or "\\" in v or ".." in v:
            raise ValueError(
                f"pipeline name must not contain '/', '\\\\', or '..' (path-safety): {v!r}"
            )
        return v
