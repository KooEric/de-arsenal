"""파이프라인 YAML의 Pydantic 모델. 선언이 인터페이스다 — extra는 거부.

YAML 전체 형태: docs/02-architecture.md "파이프라인 YAML 스펙"
필드 확장 계획: M2에서 pagination.mode(page/cursor), sink.type(duckdb/postgres),
auth, validate 블록 추가. P1 필드는 이름을 미리 예약해 하위 호환을 지킨다.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PaginationSpec(_Frozen):
    mode: Literal["offset"]  # M2: "page", "cursor" 추가
    param: str = "offset"
    size_param: str = "limit"
    size: int = 100


class RateLimitSpec(_Frozen):
    rps: float  # M2: 토큰 버킷 + 429 적응 감속


class SourceSpec(_Frozen):
    type: Literal["rest"]  # M1: 로컬 파일 소스는 M2 이후 판단
    url: str
    headers: dict[str, str] = {}
    pagination: PaginationSpec
    rate_limit: RateLimitSpec | None = None
    encoding: str = "utf-8"  # M2: euc-kr 등 비UTF-8 처리


class FileSourceSpec(_Frozen):
    """로컬 파일 소스 (M2 Task 2.11에서 SourceSpec union에 편입).

    M2 전까지는 독립 모델로만 존재 — FileSource 스켈레톤의 타입 계약용.
    """

    type: Literal["file"]
    path: str  # 글롭 (예: "./raw/**/*.csv")
    format: Literal["auto", "csv", "jsonl", "excel"] = "auto"
    encoding: str = "utf-8"


class SinkSpec(_Frozen):
    type: Literal["parquet"]  # M2: "duckdb", "postgres" 추가 (temp→MERGE 멱등)
    path: Path


class PipelineSpec(_Frozen):
    name: str
    state_dir: Path = Path(".arsenal")
    source: SourceSpec
    sink: SinkSpec
    # M2 예약: validate (검증 게이트), auth
