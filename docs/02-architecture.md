# 02. 아키텍처

## 저장소 구조 — uv workspace 모노레포

각 도구는 **독립 배포 가능한 패키지**(락인 없음, 따로 설치 가능)이되, 한 저장소에서 개발한다(공통 계약의 원자적 변경, 통합 테스트 용이).

```text
de-arsenal/
├── pyproject.toml              # uv workspace 루트 (배포 안 함)
├── packages/
│   ├── arsenal-core/           # 공통 신뢰성 코어 (모든 도구의 유일한 공통 의존성)
│   │   ├── pyproject.toml
│   │   └── src/arsenal_core/
│   │       ├── spec/           # YAML 스펙 로더 + Pydantic 모델
│   │       ├── state/          # StateStore (SQLite WAL)
│   │       ├── identity.py     # 결정적 Unit ID
│   │       ├── errors.py       # 에러 분류 체계
│   │       ├── retry.py        # backoff 재시도, 토큰 버킷 rate limiter
│   │       └── io/             # Arrow/Parquet 허브 유틸
│   ├── pugio/                  # 수집·전송
│   │   └── src/pugio/
│   │       ├── sources/        # Source 프로토콜 + rest.py, file.py
│   │       ├── sinks/          # Sink 프로토콜 + parquet.py, duckdb.py, postgres.py
│   │       ├── auth/           # AuthProvider + refresh hook
│   │       ├── validate/       # 검증 게이트 (P1에서 Scutum으로 분리)
│   │       ├── runner.py       # 수집 루프
│   │       └── cli.py
│   ├── gladius/                # 변환·쿼리
│   │   └── src/gladius/
│   │       ├── spec.py         # 변환 YAML 모델
│   │       ├── compile/        # map/steps → SQL 트랜스파일러
│   │       ├── engine.py       # DuckDB 실행 + 즉석 쿼리
│   │       └── cli.py
│   └── arsenal/                # 우산 CLI (배포명 de-arsenal) — 원클릭 진입점
│       └── src/arsenal/
│           ├── project.py      # arsenal.yaml 매니페스트 모델
│           ├── recipes/        # 원클릭 레시피 (패키지 데이터)
│           └── cli.py          # init / run / query + collect·transform 마운트
├── examples/                   # 실행 가능한 예제 YAML
├── tests/                      # 크로스 패키지 통합·E2E 테스트
│   ├── integration/
│   └── e2e/
└── docs/
```

**의존 방향** (역방향 금지):

```text
pugio ──→ arsenal-core ←── gladius
  │                            │
  └────── Arrow/Parquet ───────┘   (파일 규약으로만 연결, import 없음)
```

pugio와 gladius는 서로 import하지 않는다. 데이터 파일(Parquet)과 상태 규약으로만 맞물린다 — 이것이 "독립하되 시너지"의 구현.

## 핵심 개념 흐름 — 수집 한 사이클

```text
YAML 스펙
   │  spec 로더 (Pydantic 검증)
   ▼
Planner: 소스를 Unit of Work 목록으로 분해 ──── unit_key(예: page=7) → 결정적 unit_id
   │
   ▼
StateStore 대조: done인 unit은 건너뜀 (재개의 근거)
   │
   ▼  미완료 unit마다
Fetch (rate limit → 요청 → 에러 분류)
   │        ├─ RetryableError → backoff 재시도
   │        ├─ AuthExpiredError → refresh hook → 같은 unit 재시도
   │        └─ FatalError → failed 마킹, 즉시 중단
   ▼
검증 게이트 (스키마·null·범위·중복) ── 위반 시 정책: block / quarantine(DLQ) / warn
   │
   ▼
Sink 쓰기 (끝점별 멱등 전략) → StateStore에 done 마킹
```

**핵심 불변식**: "sink 쓰기 성공 → done 마킹" 순서. 쓰기와 마킹 사이에 죽으면 재실행 시 그 unit을 다시 쓰지만, 멱등 전략이 중복을 막는다. **at-least-once 실행 + 멱등 쓰기 = exactly-once 결과.**

## 상태 모델 (StateStore)

SQLite(WAL 모드) 단일 파일. 기본 위치 `.arsenal/state.db` (YAML `state_dir`로 변경 가능).

```sql
CREATE TABLE units (
    unit_id    TEXT PRIMARY KEY,   -- 결정적 ID (16 hex)
    pipeline   TEXT NOT NULL,
    unit_key   TEXT NOT NULL,      -- 사람이 읽는 키 (예: "page=7")
    payload    TEXT NOT NULL,      -- JSON: 요청 재구성에 필요한 파라미터
    status     TEXT NOT NULL,      -- pending | running | done | failed | quarantined
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,      -- ISO8601 UTC
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_units_pipeline_status ON units(pipeline, status);

CREATE TABLE cursors (              -- cursor 페이지네이션·CDC용 진행 지점
    pipeline   TEXT NOT NULL,
    source     TEXT NOT NULL,
    cursor     TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (pipeline, source)
);

CREATE TABLE schema_snapshots (     -- M2: 드리프트 감지의 원료 (P0는 기록만)
    pipeline    TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    schema_json TEXT NOT NULL
);
```

상태 전이:

```text
pending ──→ running ──→ done                    (정상)
              │ └──→ pending (Retryable, attempts < max)
              ├────→ failed  (Fatal 또는 attempts 소진)
              └────→ quarantined (검증 게이트 격리 → DLQ)
```

## 결정적 ID

```python
unit_id = sha256(f"{pipeline}:{source_id}:{unit_key}".encode()).hexdigest()[:16]
```

- 같은 파이프라인의 같은 unit은 언제 몇 번을 실행해도 같은 ID → 멱등의 근거
- `unit_key`는 소스가 정의: offset 페이지네이션이면 `"offset=200:limit=100"`, 시간 윈도우면 `"2026-07-01T00:00/2026-07-01T01:00"`
- cursor 모드처럼 unit을 미리 열거할 수 없는 소스는 **커서 값 자체**를 unit_key로 사용

## 끝점별 멱등 전략 (Scutum 코어)

| 끝점 능력 | 전략 | 구현 |
|---|---|---|
| 트랜잭션 MERGE 지원 (PostgreSQL, DuckDB, Snowflake) | temp → MERGE | unit 데이터를 temp 테이블에 적재 후 키 기준 MERGE. 실패 시 temp만 버려짐 |
| MERGE 약함 (BigQuery) | load to temp partition → atomic swap | P1 |
| upsert 없음 (S3·로컬 파일·객체 스토리지) | 결정적 파일명 덮어쓰기 | `{sink.path}/{unit_id}.parquet` — 같은 unit은 같은 파일명, 재실행 = 덮어쓰기 = 중복 없음 |

## 커넥터 계약 (Source / Sink 프로토콜)

```python
class Source(Protocol):
    def plan(self, ctx: RunContext) -> Iterator[UnitSpec]:
        """소스를 Unit of Work 목록으로 분해. cursor 모드는 lazy 열거."""

    def fetch(self, unit: UnitSpec, ctx: RunContext) -> pa.RecordBatch:
        """unit 하나를 Arrow RecordBatch로. 실패는 분류된 예외로 던진다."""

class Sink(Protocol):
    def write(self, unit: UnitSpec, batch: pa.RecordBatch, ctx: RunContext) -> None:
        """멱등하게 쓴다. 같은 unit 재호출 시 결과가 변하지 않아야 한다."""
```

- 교환 포맷은 **Arrow RecordBatch** 고정 — 도구 간 zero-copy, DuckDB·Parquet과 자연 결합
- 새 커넥터 추가 = 이 두 프로토콜 구현 + 스펙 모델 등록. 커넥터가 재시도·상태·멱등을 **몰라도 되게** 하는 것이 계약의 목적

## 에러 분류 체계

```python
class ArsenalError(Exception): ...
class FatalError(ArsenalError): ...          # 설정 오류, 4xx(401/429 제외), 계약 위반 → 즉시 중단
class RetryableError(ArsenalError): ...      # 네트워크, 5xx, 429, lock 충돌 → backoff 재시도
class AuthExpiredError(RetryableError): ...  # 401/만료 → 에러가 아니라 "갱신 트리거"
```

분류 규칙은 커넥터가 소유한다(REST 커넥터가 HTTP 상태 코드 → 예외 매핑). 러너는 예외 타입만 보고 행동한다.

## 파이프라인 YAML 스펙 (P0 전체 형태)

```yaml
name: github-issues
state_dir: .arsenal            # 선택, 기본 .arsenal

source:
  type: rest
  url: https://api.github.com/repos/duckdb/duckdb/issues
  headers: { Authorization: "Bearer ${GITHUB_TOKEN}" }   # 환경변수 치환, 비밀은 YAML에 안 둠
  pagination:
    mode: page                 # offset | page | cursor
    param: page
    size_param: per_page
    size: 100
  rate_limit: { rps: 5 }
  encoding: utf-8              # euc-kr 등 지원
  auth:                        # 선택
    type: oauth2_client_credentials
    token_url: https://...
    client_id_env: CLIENT_ID
    client_secret_env: CLIENT_SECRET

validate:                      # 선택 — 검증 게이트
  rules:
    - { field: id, not_null: true, unique: true }
    - { field: created_at, not_null: true }
  on_violation: quarantine     # block | quarantine | warn

sink:
  type: parquet                # parquet | duckdb | postgres
  path: ./data/issues
```

## CLI 설계

```text
pugio run <yaml>        # 실행 (재실행 = 재개, 플래그 불필요 — 재개가 기본값)
pugio status <yaml>     # unit별 상태 요약 (done/pending/failed/quarantined 수)
pugio retry <yaml>      # failed unit을 pending으로 되돌려 재시도
pugio dlq list <yaml>   # 격리된 unit 목록
pugio dlq retry <yaml> --unit <id>
gladius compile <yaml>  # 생성될 SQL 출력 (투명성 — 마법 없음)
gladius run <yaml>
```

## Gladius 컴파일 파이프라인

```text
YAML steps ──파싱──→ Step AST ──트랜스파일──→ CTE 체인 SQL ──→ DuckDB 실행 ──→ Parquet/Arrow
```

각 step은 하나의 CTE로 컴파일된다. 예:

```sql
WITH s0 AS (SELECT * FROM read_parquet('./data/issues/*.parquet')),
     s1 AS (SELECT * FROM s0 WHERE state = 'open'),                      -- filter
     s2 AS (SELECT * EXCLUDE (created_at), created_at AS opened_at FROM s1), -- rename
     s3 AS (SELECT * REPLACE (CAST(number AS BIGINT) AS number) FROM s2)     -- cast
SELECT number, title, opened_at, "user" FROM s3                              -- select
```

`gladius compile`이 이 SQL을 그대로 보여준다. 사용자는 언제든 선언을 버리고 SQL로 내려갈 수 있다(탈출구, P1).

## 설계 결정 기록 (요약 ADR)

| # | 결정 | 근거 | 대안(기각 이유) |
|---|---|---|---|
| 1 | 상태 저장 = SQLite WAL | 단일 파일·무설치·트랜잭션. "가볍게"와 "죽어도 살아남음" 동시 충족 | JSON 파일(동시성·원자성 취약), 외부 DB(무거운 전제 인프라) |
| 2 | 교환 포맷 = Arrow/Parquet | zero-copy, DuckDB 네이티브, 언어 중립 → 도구 독립성 | CSV(타입 손실), pickle(언어 락인) |
| 3 | 벡터화 엔진 = DuckDB | in-process·무서버, SQL 표준, 수백 GB 단일 노드 | Polars(SQL 탈출구와 이원화), 자체 엔진(원칙 1 위반) |
| 4 | at-least-once 실행 + 멱등 쓰기 | exactly-once "전송"은 분산 환경에서 비용이 크고 깨지기 쉬움. 결과의 exactly-once만 보장 | 2PC/트랜잭셔널 아웃박스(무거움) |
| 5 | 모노레포 + 독립 패키지 | 공통 계약의 원자적 변경 + 도구별 독립 배포 양립 | 멀티레포(계약 변경 시 N개 PR), 단일 패키지(락인) |
