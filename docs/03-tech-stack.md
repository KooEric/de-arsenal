# 03. 기술 스택

## 한눈에

| 계층 | 선택 | 버전 |
|---|---|---|
| 언어 | Python | 3.11+ (3.12 권장) |
| 패키지·워크스페이스 | uv | 최신 |
| 스펙 검증 | Pydantic | v2 |
| 상태 저장 | SQLite (표준 라이브러리 `sqlite3`, WAL) | — |
| HTTP 클라이언트 | httpx | 최신 |
| 데이터 교환 | PyArrow (Arrow/Parquet) | 최신 |
| 벡터화 실행 엔진 | DuckDB (in-process) | 최신 |
| CLI | Typer | 최신 |
| 재시도 | tenacity | 최신 |
| 린트·포맷 | ruff | 최신 |
| 타입 체크 | pyright (strict) | 최신 |
| 테스트 | pytest + pytest-cov + respx + hypothesis | 최신 |
| DB 통합 테스트 | testcontainers (PostgreSQL) | 최신 |
| CI | GitHub Actions | — |

## 선정 근거

### 언어: Python — Rust/Go 대신

- **탈출구가 Python이다.** 로드맵이 약속한 하강 경로(선언 → SQL → Python)의 끝이 Python UDF다. 코어가 Python이면 탈출구가 공짜다.
- **생태계 결합.** DuckDB·PyArrow·Parquet의 1급 바인딩이 Python. "엔진은 빌린다" 원칙상 우리가 만드는 것은 신뢰성 코어와 트랜스파일러 — CPU 바운드가 아니라 I/O·조합 문제다. 무거운 연산은 전부 DuckDB(C++)와 Arrow(C++)가 한다.
- **대상 사용자.** 데이터 엔지니어·분석가의 공용어.
- 기각: **Rust** — 성능 이점이 병목(엔진) 밖에 있음, 개발 속도·기여 장벽 손해. **Go** — Arrow/DuckDB 바인딩 성숙도 낮음, 데이터 생태계 이탈. 훗날 특정 핫패스가 병목이 되면 그 부분만 Rust 확장으로 교체(원칙 6: 가속은 선택적으로).

### 패키징: uv workspace

- 단일 lockfile로 모노레포 관리, `uv build`로 패키지별 독립 배포. pip 대비 10~100배 빠른 해석. Poetry 대비 워크스페이스 지원이 자연스러움.

### 스펙 검증: Pydantic v2

- "선언이 인터페이스"인 제품에서 YAML 검증 에러 메시지의 품질이 곧 UX. Pydantic v2는 러스트 코어로 빠르고, 필드 단위 에러 위치를 정확히 짚는다. JSON Schema 자동 생성 → 에디터 자동완성 제공 가능.
- 기각: dataclass+수동 검증(에러 품질 낮음), jsonschema 단독(파이썬 타입과 이원화).

### 상태: SQLite (stdlib) + WAL

- 무설치·단일 파일·ACID 트랜잭션 — "가볍게"와 "상태는 메모리에 두지 않는다"를 동시에.
- WAL 모드로 읽기(status 명령)와 쓰기(runner) 동시 접근 허용.
- ORM 없이 raw SQL — 테이블 3개에 ORM은 과함(YAGNI).

### HTTP: httpx

- 동기/비동기 동일 API — P0는 동기로 단순하게 시작하고, P2 스트리밍에서 async 전환 시 클라이언트 교체 불필요.
- respx로 테스트 모킹이 깔끔함. requests는 async 경로가 없어 기각.

### 데이터: PyArrow + DuckDB

- Arrow RecordBatch가 커넥터 계약의 교환 타입. Parquet 읽기/쓰기, DuckDB와 zero-copy 연동.
- DuckDB: in-process(서버 없음), 표준 SQL, 벡터화·병렬 기본. 단일 노드 수백 GB 처리라는 Gladius 약속의 실체. `EXCLUDE`/`REPLACE` 등 SQL 확장이 트랜스파일러 출력을 단순하게 해줌.
- 기각: Polars — 훌륭하지만 SQL이 2급 시민. "steps→SQL 컴파일, SQL 탈출구" 설계와 이원화됨.

### CLI: Typer

- 타입 힌트 기반 선언적 CLI, 자동 help, 서브커맨드 그룹(`pugio dlq list`). click 위에 구축되어 성숙.

### 재시도: tenacity

- backoff·jitter·예외 필터를 선언적으로. 직접 구현하지 않는다(원칙 1). 단, rate limiter(토큰 버킷)는 tenacity 영역이 아니므로 소형 직접 구현.

### 품질 도구: ruff + pyright strict

- ruff 하나로 lint+format(+isort) 통합 — 도구 수 최소화.
- pyright strict: 커넥터 프로토콜(Protocol) 준수를 컴파일 타임에 강제 — 계약 기반 설계의 안전망.

### 테스트: pytest 생태계

- respx: httpx 요청 모킹 — 페이지네이션·429·401 시나리오를 네트워크 없이 재현.
- hypothesis: 결정적 ID·트랜스파일러의 속성 기반 테스트(같은 입력→같은 출력, SQL 동치성).
- testcontainers: PostgreSQL MERGE 멱등 전략의 실물 검증 (CI에서 Docker).
- 상세는 [05-testing-plan.md](05-testing-plan.md).

## 의존성 정책

- **arsenal-core의 런타임 의존성은 최소로**: pydantic, pyyaml, tenacity. (pyarrow는 io 모듈 사용 시에만 — extras로 분리 검토)
- pugio: + httpx, pyarrow, typer / gladius: + duckdb, pyarrow, typer
- 새 의존성 추가는 ADR 한 줄(02 문서의 결정 기록 표)로 기록 후 추가.
- 표준 라이브러리로 충분한 것에 외부 패키지를 들이지 않는다 (sqlite3, hashlib, json, datetime).

## 버전·호환성 정책

- Python 3.11 최소 지원 (`tomllib`, `ExceptionGroup`, 성능 개선선).
- SemVer. v0.x 동안 마이너 버전에서 스펙 필드 변경 가능하되 CHANGELOG에 마이그레이션 노트 필수.
- YAML 스펙은 v1.0부터 하위 호환 보장 — "사용자가 보는 것은 선언뿐"이므로 선언의 안정성이 곧 제품 신뢰.
