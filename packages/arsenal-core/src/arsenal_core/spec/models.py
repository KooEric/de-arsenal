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

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PaginationSpec(_Frozen):
    mode: Literal["offset"]  # M2: "page", "cursor" 추가
    param: str = "offset"
    size_param: str = "limit"
    size: int = 100


class RateLimitSpec(_Frozen):
    rps: float  # M2: 토큰 버킷 + 429 적응 감속


class RestSourceSpec(_Frozen):
    """REST API 소스 — M1 SourceSpec의 필드를 그대로 옮긴 것 (하위 호환)."""

    type: Literal["rest"]
    url: str
    headers: dict[str, str] = {}
    pagination: PaginationSpec
    rate_limit: RateLimitSpec | None = None
    encoding: str = "utf-8"  # M2: euc-kr 등 비UTF-8 처리


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


class SinkSpec(_Frozen):
    type: Literal["parquet"]  # M2: "duckdb", "postgres" 추가 (temp→MERGE 멱등)
    path: Path


class PipelineSpec(_Frozen):
    name: str
    state_dir: Path = Path(".arsenal")
    source: SourceSpec
    sink: SinkSpec
    # M2 예약: validate (검증 게이트), auth
