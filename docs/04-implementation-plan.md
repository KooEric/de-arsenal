# 04. 구현 계획 (Implementation Plan)

> 마일스톤 범위·기간은 [01-scope.md](01-scope.md) 참조. 이 문서는 "어떤 순서로, 어떻게" 만드는지의 작업 분해(WBS)다.
>
> **M0~M4 전체의 코드 수준 상세 계획이 [plans/](plans/)에 있다** — 전체 틀을 먼저 확정하고 조금씩 수정하며 접근하는 방식.
> M1은 확정본, M2~M4는 **초안**(스켈레톤 커밋 c05de04의 시그니처 기준)이다. 각 마일스톤 착수 시 선행 마일스톤의 실코드와 대조해 갱신한 뒤 실행하고, 문서 상단의 "초안 상태" 블록을 제거한다.

## 진행 방식

- **TDD 필수**: 실패 테스트 → 최소 구현 → 통과 → 리팩터 → 커밋. 커버리지 80%+.
- **수직 슬라이스 우선**: 패키지를 완성하고 넘어가는 게 아니라, 매 마일스톤 끝에 실행 가능한 데모가 나온다.
- **작은 커밋**: 태스크당 1~3 커밋. conventional commit(`feat:`, `fix:`, `test:`, `chore:`).
- **브랜치**: `feat/m1-state-store`처럼 마일스톤-태스크 단위. main은 항상 초록.

## M0 — 부트스트랩 (2~3일)

| # | 태스크 | 내용 |
|---|---|---|
| 0.1 | 워크스페이스 루트 | 루트 `pyproject.toml` (uv workspace, dev 의존성: ruff/pyright/pytest/pytest-cov) |
| 0.2 | 패키지 스켈레톤 | `packages/{arsenal-core,pugio,gladius}` 각각 pyproject + `src/` + 빈 테스트 1개 (`test_import`) |
| 0.3 | 품질 설정 | ruff 설정(line 100), pyright strict, pytest 설정(coverage fail-under=80) |
| 0.4 | CI | GitHub Actions: lint → typecheck → test, 매트릭스(ubuntu/macos × py3.11/3.12) |
| 0.5 | pre-commit + 초기 커밋 | ruff 훅, 문서 포함 첫 push |

**DoD**: `uv sync && uv run pytest` 통과, CI 초록.

## M1 — 신뢰성 코어 + 수집 수직 슬라이스 (2주)

구현 순서는 의존 방향을 따른다: 스펙 → 에러 → ID → 상태 → 커넥터 → 러너 → CLI.

| # | 태스크 | 패키지 | 산출물 |
|---|---|---|---|
| 1.1 | 에러 분류 체계 | core | `errors.py`: `FatalError`/`RetryableError`/`AuthExpiredError` |
| 1.2 | 결정적 Unit ID | core | `identity.py`: `unit_id(pipeline, source, unit_key)` — hypothesis 속성 테스트 포함 |
| 1.3 | StateStore | core | `state/store.py`: SQLite WAL, upsert/claim/mark_done/mark_failed/pending 조회 + 단위 지표(row_count/byte_count/duration_ms) 기록 |
| 1.4 | YAML 스펙 로더 | core | `spec/`: Pydantic 모델(P0 필드), env 치환(`${VAR}`), 친절한 검증 에러 |
| 1.5 | 재시도 래퍼 | core | `retry.py`: tenacity 기반, RetryableError만 재시도 |
| 1.6 | Source 프로토콜 + REST(offset) | pugio | `sources/base.py`, `sources/rest.py`: plan()이 offset unit 열거, fetch()가 RecordBatch 반환, HTTP 상태→예외 매핑 |
| 1.7 | Sink 프로토콜 + Parquet | pugio | `sinks/base.py`, `sinks/parquet.py`: `{path}/{unit_id}.parquet` 결정적 파일명 |
| 1.8 | Runner | pugio | `runner.py`: plan→미완료 필터→fetch→write→mark_done 루프 |
| 1.9 | CLI | pugio | `cli.py`: `pugio run`, `pugio status` |
| 1.10 | 재개 E2E | tests | kill 시뮬레이션 후 재실행 → 중복·누락 0 검증 |

**DoD**: 태스크 1.10 테스트가 통과하고, `examples/` 예제로 수동 재현 가능.

## M2 — Pugio P0 완성 (3주)

| # | 태스크 | 내용 |
|---|---|---|
| 2.1 | 페이지네이션 확장 | `mode: page`(파라미터 이름 변형), `mode: cursor`(응답에서 다음 커서 추출 — JSONPath 계열 표현식, cursors 테이블 연동) |
| 2.2 | 인코딩 | `encoding` 필드 — euc-kr 응답 디코딩, 테스트 픽스처 포함 |
| 2.3 | Rate limiter | 토큰 버킷(`rps`), 429 수신 시 `Retry-After` 존중 + 적응 감속 |
| 2.4 | Auth 계층 | `AuthProvider` 프로토콜: static token / oauth2 client credentials(만료 전 선제 갱신) / 401→`AuthExpiredError`→refresh→동일 unit 재시도 |
| 2.5 | DuckDB sink | temp table → MERGE (키 = validate 규칙의 unique 필드 또는 명시 `merge_key`) |
| 2.6 | PostgreSQL sink | temp → `INSERT ... ON CONFLICT DO UPDATE`. testcontainers 통합 테스트 |
| 2.7 | 검증 게이트 | `validate.rules`(not_null/unique/range/schema) — Arrow 수준 벡터화 검사, 정책 block/quarantine/warn |
| 2.8 | DLQ | quarantined unit 저장(`.arsenal/dlq/` parquet + 사유), `pugio dlq list/retry` |
| 2.9 | 스키마 스냅샷 | 수집 시 Arrow 스키마를 schema_snapshots에 기록 (감지·정책은 P1) |
| 2.10 | FileSource | 로컬 파일 소스(csv/jsonl/excel → Arrow) — 파일 하나=unit 하나, 글롭 열거 |
| 2.11 | 실 API E2E | GitHub API 대상 스모크 (CI에선 opt-in) |

**DoD**: 시나리오 B(재개)·C(토큰 갱신) + 검증 격리가 통합 테스트로 자동 검증.

## M3 — Gladius P0 (2주)

| # | 태스크 | 내용 |
|---|---|---|
| 3.1 | 변환 스펙 모델 | `map`(필드 매핑표) + `steps`(filter/rename/cast/select/dedup/derive) Pydantic 모델 |
| 3.2 | 트랜스파일러 | Step AST → CTE 체인 SQL. 식별자 인용·인젝션 안전 처리. hypothesis로 라운드트립 검증 |
| 3.3 | 실행 엔진 | DuckDB로 SQL 실행: `read_parquet` in → `COPY TO` parquet out |
| 3.4 | CLI | `gladius compile`(SQL 출력), `gladius run` |
| 3.5 | 즉석 쿼리 | `gladius query "SELECT ..."` — Parquet 레이크 직접 SQL (P1에서 앞당김) |
| 3.6 | Golden 테스트 | 각 step 조합 결과를 손으로 쓴 SQL 결과와 대조 |
| 3.7 | 벤치마크 | 1GB Parquet 변환 시간 측정 스크립트 + 수치 기록 |

**DoD**: golden 테스트 전체 통과, `gladius compile` 출력이 문서 예제와 일치.

## M4 — 통합·릴리스 (1주)

| # | 태스크 | 내용 |
|---|---|---|
| 4.0 | `arsenal` 우산 CLI | 신규 패키지: init(레시피)/run(매니페스트)/collect/transform/query — pugio·gladius 위임 |
| 4.1 | 원클릭 레시피 3종 | github-issues / csv-cleanup / api-to-postgres — 각각 E2E 검증 포함 |
| 4.2 | 연계 예제 | Pugio 수집 → Gladius 변환 end-to-end 예제 + Arrow/Parquet 허브 규약 문서 |
| 4.3 | 사용 문서 | 도구별 README, YAML 레퍼런스(스펙 모델에서 JSON Schema 자동 생성), `uv tool install` 설치 경로 |
| 4.4 | 한계선 문서 | 처리 범위 정직하게: 단일 노드 한계, 미지원 케이스 (Ballista 문제 2) |
| 4.5 | 패키징·CI | `uv build` 검증, 패키지 메타데이터, 라이선스, **Windows CI 추가** |
| 4.6 | v0.1.0 | CHANGELOG, git tag, (선택) PyPI 배포 |

## 리스크와 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| cursor 페이지네이션의 unit 열거 불가 문제 | M2 설계 재작업 | M1에서 unit_key 설계에 cursor 케이스를 미리 반영(커서 값=unit_key). 아키텍처 문서에 선반영 완료 |
| MERGE 멱등이 sink별로 미묘하게 다름 | 중복 버그 | sink 공통 계약 테스트 스위트(같은 unit 2회 write → 결과 동일)를 모든 sink에 강제 |
| 트랜스파일러 SQL 인젝션·식별자 처리 | 보안·정합성 | 식별자는 화이트리스트+인용, 값은 파라미터 바인딩. hypothesis 퍼징 |
| 스펙 필드가 P1에서 깨지는 설계 | 사용자 이탈 | 스펙 모델에 `extra="forbid"` + 버전 필드. P1 항목(드리프트 정책 등)의 필드 이름을 지금 예약 |
| 1인 개발 페이스 | 일정 지연 | 마일스톤이 각각 독립 데모 — 지연돼도 중간 산출물이 유효. AI 에이전트로 태스크 병렬화 |

## 마일스톤 진행 체크리스트

- [x] M0 부트스트랩 (스켈레톤 커밋 c05de04 — 워크스페이스·CI·품질 게이트 동작)
- [ ] M1 신뢰성 코어 + 수집 수직 슬라이스 → [상세 계획](plans/2026-07-08-m1-core-foundation.md)
- [ ] M2 Pugio P0 완성 → [상세 계획 (초안)](plans/2026-07-08-m2-pugio-complete.md)
- [ ] M3 Gladius P0 → [상세 계획 (초안)](plans/2026-07-08-m3-gladius.md)
- [ ] M4 통합·릴리스 → [상세 계획 (초안)](plans/2026-07-08-m4-integration-release.md)
