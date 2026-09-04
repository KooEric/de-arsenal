"""파이프라인 YAML의 Pydantic 모델. 선언이 인터페이스다 — extra는 거부.

YAML 전체 형태: docs/02-architecture.md "파이프라인 YAML 스펙"
SourceSpec은 discriminated union(rest/file/database/python/dlt)이다. 기존 이름
`SourceSpec`은 union 별칭으로 유지해 M1 코드의 import가 깨지지 않게 한다 — 단,
`SourceSpec(type="rest", ...)` 같은 직접 생성자 호출은 더 이상 불가하다
(Annotated[Union[...], ...]는 호출 불가). REST 소스를 직접 만들 때는
`RestSourceSpec`을 사용한다 (M1의 REST 필드를 그대로 옮긴 것 — 필드 호환).
필드 확장 계획: M2에서 pagination.mode(page/cursor), sink.type(duckdb/postgres),
auth, validate 블록 추가. P1 필드는 이름을 미리 예약해 하위 호환을 지킨다.
"""

from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from arsenal_core.errors import FatalError
from arsenal_core.timewindow import TimestampFormat, parse_duration, parse_timestamp


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


class RestIncrementalSpec(_Frozen):
    """REST 증분 수집 — "지난 실행 이후 새 데이터만"을 시간 창으로 표현한다.

    unit이 곧 시간 구간 `[since, until)`이고, 구간마다 페이지네이션을 완주한다.
    한 구간을 완주하면 워터마크가 그 구간의 끝으로 전진해 다음 실행은 거기서
    시작한다 — 완료된 구간은 다시 열거되지 않는다.

    창은 `start + k*window` 그리드에 정렬되고 **완결된 창만** 수집한다. 따라서
    데이터는 최대 `window + lag`만큼 늦다 (정직한 신선도 상한). 더 신선해야 하면
    `window`를 줄이고 그만큼 자주 실행한다.

    `lag`는 소스에서 늦게 도착하는 데이터를 위한 안전 여유다 — 이미 수집한 구간을
    다시 받는 기능은 없다(결정적 unit ID와 양립하지 않는다). 늦게 도착하는 데이터가
    있으면 `lag`를 그만큼 늘린다.
    """

    since_param: str = Field(description="창 시작을 실어 보낼 요청 파라미터명")
    until_param: str | None = Field(
        default=None, description="창 끝을 실어 보낼 요청 파라미터명 (없으면 시작만 보낸다)"
    )
    start: str = Field(description="첫 실행의 시작 시각 (ISO 8601). 이후에는 워터마크가 이긴다")
    window: str = Field(default="1d", description="창 크기 (s|m|h|d|w, 예: 1h)")
    lag: str = Field(default="0s", description="지금으로부터 이만큼은 수집하지 않는다")
    format: TimestampFormat = Field(default="iso8601", description="요청 파라미터에 실을 시각 표기")

    @field_validator("window", "lag")
    @classmethod
    def _valid_duration(cls, v: str) -> str:
        try:
            parse_duration(v)
        except FatalError as e:
            raise ValueError(str(e)) from e
        return v

    @field_validator("start")
    @classmethod
    def _valid_start(cls, v: str) -> str:
        try:
            parse_timestamp(v)
        except FatalError as e:
            raise ValueError(str(e)) from e
        return v

    @model_validator(mode="after")
    def _window_is_positive(self) -> "RestIncrementalSpec":
        if parse_duration(self.window) <= timedelta(0):
            raise ValueError("window must be greater than zero")
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
    # M2-H: Notion 검색(`POST /v1/search`)처럼 페이지네이션 파라미터를 쿼리가 아니라
    # JSON 바디로 실어야 하는 API가 있다 — GET이 기본값이라 기존 스펙은 영향받지 않는다.
    method: Literal["GET", "POST"] = "GET"
    body: dict[str, Any] | None = None  # method="POST"일 때 요청에 실을 정적 JSON 바디
    # P1: 시간 창 기반 증분 수집. 없으면 기존 동작(데이터셋을 한 번 통째로 수집).
    incremental: RestIncrementalSpec | None = None

    @model_validator(mode="after")
    def _incremental_requires_ordinal_pagination(self) -> "RestSourceSpec":
        """증분은 offset/page에서만. cursor/link와는 구조적으로 양립하지 않는다.

        (1) 두 방식 모두 상태 저장소의 같은 커서 행 하나를 쓴다 — 창 워터마크와
        페이지 커서가 서로를 덮어쓴다. (2) 전진만 하는 커서는 시간 구간으로 되감을
        수 없다. 조용히 어긋나게 두느니 스펙 로드 시점에 거부한다.
        """
        if self.incremental is not None and self.pagination.mode not in ("offset", "page"):
            raise ValueError(
                "incremental requires pagination mode 'offset' or 'page' "
                f"(got {self.pagination.mode!r}); a forward-only cursor cannot be "
                "rewound to a time window"
            )
        return self


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
    """운영 DB 소스 (M2 Task 2.12 / M2-G).

    dialect="sqlite"는 Python 표준 sqlite3 드라이버. dialect="postgres"/"mysql"은
    DuckDB scanner(ATTACH, docs/09 수 1)로 구현된다.
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


class DltSourceSpec(_Frozen):
    """Optional dlt source adapter (target is a user-owned source function)."""

    type: Literal["dlt"]
    target: str  # "package.module:source_function"
    options: dict[str, Any] = {}
    resources: list[str] | None = None
    batch_size: int = Field(default=10_000, gt=0)


SourceSpec = Annotated[
    RestSourceSpec | FileSourceSpec | DatabaseSourceSpec | PythonSourceSpec | DltSourceSpec,
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


def _merge_key_non_empty(v: list[str]) -> list[str]:
    """merge_key가 비어 있으면 DELETE/ON CONFLICT의 매치 조건이 없어 전체 테이블을
    지우거나(duckdb) 제약을 만들 수 없다(postgres) — 생성 시점에 차단한다 (M2-F FIX 4)."""
    if len(v) < 1:
        raise ValueError("merge_key must have at least one column")
    return v


class DuckDBSinkSpec(_Frozen):
    """DuckDB 파일 싱크 (M2) — temp 테이블 → MERGE로 멱등 upsert."""

    type: Literal["duckdb"]
    path: str
    table: str
    merge_key: list[str]

    @field_validator("merge_key")
    @classmethod
    def _merge_key_non_empty(cls, v: list[str]) -> list[str]:
        return _merge_key_non_empty(v)


class PostgresSinkSpec(_Frozen):
    """Postgres 싱크 (M2) — merge_key 기준 upsert."""

    type: Literal["postgres"]
    dsn_env: str
    table: str
    merge_key: list[str]

    @field_validator("merge_key")
    @classmethod
    def _merge_key_non_empty(cls, v: list[str]) -> list[str]:
        return _merge_key_non_empty(v)


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


class ContractColumnSpec(_Frozen):
    name: str
    type: str = Field(description="Arrow type string, for example int64 or string")
    nullable: bool = True


class ContractSpec(_Frozen):
    columns: list[ContractColumnSpec] = Field(min_length=1)
    allow_extra: bool = False
    on_violation: Literal["block", "quarantine", "warn"] = "block"


class PipelineSpec(_Frozen):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    name: str
    # str로 보관 — ParquetSinkSpec.path와 같은 규약. Path 기본값은 JSON 스키마
    # 직렬화가 호스트의 경로 flavour에 묶여, Windows에서 생성한 산출물이
    # Linux/macOS 것과 달라진다(생성 문서 `--check` CI 게이트가 깨진다).
    # 파일시스템 연산이 필요한 지점에서 Path(...)로 감싸 해석한다.
    state_dir: str = ".arsenal"
    source: SourceSpec
    sink: SinkSpec
    validation: ValidateSpec | None = Field(default=None, alias="validate")
    contract: ContractSpec | None = None
    schema_drift: Literal["allow", "warn", "block"] = Field(
        default="allow",
        description="Policy when the observed source schema differs from the last snapshot.",
    )

    @field_validator("state_dir", mode="before")
    @classmethod
    def _coerce_state_dir_to_str(cls, v: object) -> object:
        if isinstance(v, Path):
            return str(v)
        return v

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
