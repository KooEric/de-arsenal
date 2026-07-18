# 01. 구축 범위 (Scope)

## 범위 결정 원칙

- **P0 = MVP 핵심**: "끊김·중복에 견디는 신뢰성"이라는 약속이 성립하는 최소 집합. 이것부터 만든다.
- 로드맵의 P0 항목을 **동작하는 수직 슬라이스(vertical slice)** 단위 마일스톤으로 재배열한다. 패키지별로 완성하는 게 아니라, 매 마일스톤마다 사용자가 실행해볼 수 있는 것을 만든다.
- Scutum의 멱등 가드·검증 게이트와 Spatha의 멱등 재실행은 P0에서 **별도 패키지가 아니라 코어에 내장**한다. P1에서 독립 패키지로 분리한다.

## 마일스톤 개요 (P0)

| 마일스톤 | 이름 | 내용 | 산출물 | 예상 기간 |
|---|---|---|---|---|
| **M0** | 부트스트랩 | 모노레포·CI·품질 도구 | 빈 패키지 3개 + 초록 CI | 2~3일 |
| **M1** | 신뢰성 코어 + 수집 수직 슬라이스 | StateStore, 결정적 ID, 에러 분류, REST→Parquet 재개 수집 | `pugio run`으로 끊겨도 재개되는 수집 데모 | 2주 |
| **M2** | Pugio P0 완성 | 페이지네이션 4종, 인증 갱신, rate limit, 인코딩, **파일·DB 소스, Python 탈출구**, DB sink 멱등, 검증 게이트, DLQ, 실전 API 5종 검증 | 실전 투입 가능한 Pugio v0.1 | 3.5주 |
| **M3** | Gladius P0 | map/steps→SQL 트랜스파일러, DuckDB 실행, **즉석 쿼리(`gladius query`)** | `gladius run`으로 선언형 변환 + 미니 DWH | 2주 |
| **M4** | 통합·릴리스 | **`arsenal` 우산 CLI + 원클릭 레시피 3종**, 연계 예제, 문서, 벤치마크, v0.1 태그 | "10분의 마법"이 성립하는 v0.1 | 1.5주 |

총 P0 기간: **약 8~9주** (1인 풀타임 기준. AI 에이전트 병행 시 단축 가능)

## 마일스톤별 상세 범위

### M0 — 부트스트랩

**포함:**
- uv workspace 모노레포: `packages/arsenal-core`, `packages/pugio`, `packages/gladius`
- ruff(lint+format), pyright(타입 체크), pytest(+coverage 80% 게이트)
- GitHub Actions CI: lint → typecheck → test 매트릭스(macOS/Linux, Python 3.11/3.12)
- pre-commit 훅

**완료 기준(DoD):** `uv sync && uv run pytest` 통과, CI 초록, 각 패키지가 서로 import 가능.

### M1 — 신뢰성 코어 + 수집 수직 슬라이스

로드맵 대응: Pugio 문제 2(끊기면 처음부터), Scutum 문제 3(끝점별 멱등— 객체 스토리지 경로), 설계 원칙 2·4.

**포함 (arsenal-core):**
- 파이프라인 YAML 스펙 로더 (Pydantic 스키마 검증, 명확한 에러 메시지)
- 결정적 Unit ID: `unit_id = sha256("{pipeline}:{source}:{unit_key}")[:16]`
- StateStore: SQLite(WAL) 기반 unit 상태 기록 — pending/running/done/failed/quarantined + 단위 지표(행 수·바이트·소요 시간) 기록 ([07-cost-efficiency.md](07-cost-efficiency.md) 비용 가시성의 원료 — 나중에 소급 불가하므로 P0부터)
- 에러 분류 체계: `RetryableError` / `AuthExpiredError` / `FatalError`
- backoff 재시도 래퍼 (retryable만 재시도)

**포함 (pugio):**
- `Source` 프로토콜 + REST 소스 (offset 페이지네이션 1종)
- `Sink` 프로토콜 + Parquet sink (결정적 파일명 = `hash(unit_id).parquet` → 재실행 시 덮어쓰기 = 멱등)
- Runner: plan units → 미완료만 fetch → write → done 마킹 루프
- CLI: `pugio run <yaml>`, `pugio status <yaml>`

**완료 기준(DoD):** 수집 중 `kill -9` 후 재실행하면 마지막 완료 unit 다음부터 재개되고, 결과 Parquet에 중복·누락이 없음을 자동 테스트로 증명.

### M2 — Pugio P0 완성

로드맵 대응: Pugio 문제 1(인증 만료)·문제 3(페이지네이션·인코딩·rate limit), Scutum 문제 1(검증 게이트)·문제 3(DB 멱등 전략).

**포함:**
- 페이지네이션: `mode: offset | page | cursor | link` 4종 (link = RFC 5988 `Link` 헤더 — GitHub/Shopify/GitLab의 표준 방식)
- **FileSource: 로컬 파일 수집(csv/jsonl/excel → Arrow)** — 파일 하나=unit 하나(멱등 공짜). 분석가의 1번 고통("CSV 뭉치를 쿼리 가능하게")의 입구
- **DatabaseSource: 운영 DB → 웨어하우스 동기화** — DE의 1번 수집 작업. DuckDB scanner 차용([09](09-oss-leverage.md) 수 1), 키 범위 분할 = unit
- **Python 커스텀 소스 탈출구** (`type: python`) — YAML로 표현 안 되는 API를 만나도 절벽이 없다. P1 dlt 래퍼의 기반 메커니즘
- **실전 API 5종 스펙 검증** — GitHub(Link 헤더)·Stripe(cursor)·공공데이터포털(euc-kr+page)·Notion(rate limit) 등을 실제 YAML로 작성, 표현 불가 지점을 스펙에 역반영
- `encoding` 필드 (euc-kr 등 비UTF-8 소스)
- 토큰 버킷 rate limiter (`rate_limit.rps`, 429 응답 시 적응적 감속)
- Auth Refresh Hook: 401/만료 → `AuthExpiredError` → 갱신 콜백 → 그 unit부터 재개
- 인증 방식: static token, OAuth2 client credentials(자동 갱신), 커스텀 훅
- DB sink: DuckDB·PostgreSQL — temp table → MERGE 멱등 전략
- 검증 게이트: 선언형 규칙(`not_null`, `unique`, `range`, `schema`) + 정책(`block`/`quarantine`/`warn`)
- DLQ: 격리 unit 보관(`pugio dlq list/retry`)
- 스키마 스냅샷 기록 (드리프트 **감지·정책은 P1**, 기록만 P0)

**완료 기준(DoD):** 4종 페이지네이션 + 토큰 만료 시나리오 + 검증 위반 격리가 통합 테스트로 커버. 실전 API 5종이 YAML로 표현됨(불가 지점은 탈출구 판정 기록). 실제 공개 API(GitHub) 대상 E2E 1개.

### M3 — Gladius P0

로드맵 대응: Gladius 문제 1(변환이 코드여야만)·문제 2(변환이 느리다).

**포함:**
- 변환 YAML 스펙: `map`(필드 매핑표) + `steps`(레시피 체인: filter/rename/cast/select/dedup/derive)
- steps → SQL 트랜스파일러 (각 step이 CTE로 컴파일, 검사 가능한 SQL 출력)
- DuckDB 실행 엔진 (Parquet in → Parquet out, Arrow 경유)
- `gladius compile <yaml>` — 생성된 SQL을 보여주는 투명성 커맨드
- `gladius run <yaml>`
- **`gladius query "SELECT ..."` — 수집한 Parquet에 즉석 SQL(미니 DWH). P1에서 앞당김: 원클릭 경험의 "아하 모먼트"이고 DuckDB 위에서 비용이 거의 0**

**완료 기준(DoD):** map/steps로 작성한 변환이 손으로 쓴 SQL과 동일 결과(golden test). 수집 직후 `gladius query`로 결과 확인 가능. 1GB Parquet 변환이 단일 노드에서 완료되는 벤치마크 기록.

### M4 — 통합·릴리스: "원클릭 경험" 완성

**포함:**
- **`arsenal` 우산 CLI** (신규 패키지 `packages/arsenal`, 배포명 `de-arsenal`): 단일 진입점. `init`(레시피 스캐폴드)/`run`(매니페스트 기반 수집→변환 일괄)/`collect`/`transform`/`query`. pugio·gladius를 감싸는 얇은 위임 계층 — 새 로직 없음, Unix 철학(개별 도구 독립) 유지
- **원클릭 레시피 3종**: `github-issues`(API→테이블), `csv-cleanup`(CSV 뭉치→정리된 Parquet+쿼리), `api-to-postgres`(API→PG upsert 동기화) — "자주 접하는 문제의 원클릭 솔루션"의 실체
- Pugio 수집 → Gladius 변환 연계 예제 (Arrow/Parquet 허브 규약 문서화)
- **설치 경로를 분석가 기준으로**: `uv tool install de-arsenal` / pipx를 공식 1줄 설치로 문서화. **CI에 Windows 추가**
- README·각 도구 사용 문서·처리 범위 정직한 문서화(Ballista 문제 2의 P0 몫) — [08-limits.md](08-limits.md)
- 벤치마크 스크립트와 수치 기록
- PyPI 패키징 검증(`uv build`), v0.1.0 태그

**완료 기준(DoD):** 신규 사용자가 `uv tool install` + `arsenal init <recipe>` + `arsenal run` 세 명령으로 10분 안에 시나리오 A~D를 재현 가능.

## P1 — 확장 (P0 완료 후, 범위만 확정)

| 도구 | 항목 |
|---|---|
| Pugio | 스키마 드리프트 감지 + 정책(통과·경고·차단), **dlt 소스 래퍼**(`type: dlt` — 검증된 커넥터 수백 개 흡수, [09](09-oss-leverage.md) 수 2), **S3/GCS sink**(DuckDB httpfs 차용) |
| Arsenal | **dbt 인터롭** — `arsenal.yaml`의 `- dbt: ./project` 실행 단계(dbt-duckdb 차용, [09](09-oss-leverage.md) 수 3) |
| Gladius | SQL 탈출구(steps 안 `sql:` step), Python 탈출구(UDF), **증분 변환**(`incremental: by_unit/by_key` — 신규분만 재계산, [07](07-cost-efficiency.md) 참조) — 쿼리 모드는 M3로 앞당겨짐 |
| Spatha | 독립 패키지 분리: 데이터 준비 신호 기반 의존성 DAG, 우선순위 큐·실행 윈도우 |
| Scorpio | 신규: lineage 자동 기록, freshness·지연 메트릭, 알림, `--cost` 요약(P0에 기록한 단위 지표 노출) |
| Onager | 신규: 백필 격리 실행, Small File compaction(후보 자동 선정·dry_run·안전장치) |
| Scutum | 독립 패키지 분리: data contract, lock 충돌 retry/backoff 흡수 |
| Ballista | 처리 한계선 문서화 지속 갱신 |

## P2 — 고급·대규모 (방향만 확정)

- Hasta: 폴링 마이크로배치 스트리밍, CDC 1급 시민화
- Pilum: reverse ETL(sink의 외부 끝점 일반화), 디스패치 재시도+DLQ
- Scorpio: 비용 추적(단위 지표 × 단가 환산, 무거운 파이프라인 랭킹)
- Ballista: Executor 인터페이스로 분산 백엔드 교체(로컬→Spark/Flink), 사용자 YAML 불변
- Onager: 스키마 마이그레이션(버전 관리된 변환)
- **Aquila(신규 제안, 카탈로그·거버넌스)**: Unity Catalog의 미니 대응. 파일 기반(SQLite) 경량 카탈로그 — 데이터셋 등록·검색, 스키마 버전 레지스트리, Scorpio가 수집한 lineage와 Scutum data contract의 저장·조회 계층. 서버 없이 저장소 파일 하나로 시작
- 오픈 테이블 포맷 sink: Iceberg(우선)·Delta Lake — plain Parquet에서 자연 상향 경로, DuckDB 확장 활용. 락인 없는 lakehouse 완성

## Out of Scope (명시적 제외)

- **웹 UI / 서버 데몬** — CLI+YAML이 P0의 유일한 인터페이스. UI는 제품군 성숙 후 별도 판단
- **커넥터 백화점** — P0 소스는 REST(+로컬 파일)만. 커넥터 수 경쟁은 하지 않는다. 커넥터 계약을 잘 만들어 확장 비용을 낮추는 것이 우선
- **자체 실행 엔진** — 벡터화·분산 엔진을 직접 만들지 않는다 (설계 원칙 1)
- **스케줄러 데몬** — P0의 스케줄링은 외부 cron/CI에 위임. Spatha P1에서 신호 기반 트리거 도입
- **클라우드 매니지드 서비스** — OSS 도구 완성이 먼저
- **트랜잭션 DB (Lakebase 대응)** — OLTP는 만들지 않는다. 기존 Postgres 등은 Pugio의 source/sink로 연결만
- **AI/모델 거버넌스 (Unity AI Gateway 대응)** — 데이터 도구에 집중. AI 자산 카탈로그는 범위 밖
- **BI/시각화 레이어** — 쿼리 모드(P1)까지가 경계. 대시보드는 기존 도구(Metabase 등)가 Parquet/DuckDB를 직접 읽게 한다
