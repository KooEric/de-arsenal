# 00. 프로젝트 개요

> 원본 문제 정의 전체는 [roadmap.md](roadmap.md) 참조. 이 문서는 로드맵을 "무엇을 어떻게 만들 것인가" 관점으로 압축한 것이다.

## 비전

데이터 엔지니어가 겪는 **각각의 문제**에 대해, 작고 날카로운 도구가 **가장 우아한 해결책 하나**를 보여준다. 핵심 약속은 **"한 문제, 최고의 해결책"**.

## 포지셔닝

> **v0.3.0 이후의 포지션 전환은 [10-direction.md](10-direction.md)에 있다.** 사용자를 향한 기준점은 "Databricks 미니"에서 "데이터의 Canva — 가입도 과금도 없는 플랫폼, 당신의 AI가 운전한다"로 옮겼다. 아래 표와 Databricks 대응표는 **내부 설계 지도**로 유지한다.

| | 거대 플랫폼 (Databricks/Snowflake) | DE Arsenal |
|---|---|---|
| 형태 | 모든 것을 떠안는 하나의 플랫폼 | 문제별 독립 도구의 제품군 |
| 도입 | 전체를 사야 함, 무거운 전제 인프라 | 한 도구만 집어 바로 사용 |
| 락인 | 강함 (한번 들어가면 나오기 어려움) | 없음 (어떤 도구든 빼거나 교체 가능) |
| 완성도 | 백 가지 × 80점 | 한 가지 × 100점, 그런 도구 여러 개 |

**Unix 철학**: 각 도구는 독립 제품이다. 따로 도입해도 제값을 하고, 작은 공통 인터페이스(Arrow/Parquet 허브, 공유 상태 규약)로 느슨하게 맞물려 같이 쓰면 시너지가 난다.

## 경쟁 제품 대비 차별점

- **vs Airbyte/Fivetran (수집)**: 커넥터 수 경쟁 대신 신뢰성 코어 경쟁. 재개·멱등·인증 자동 갱신이 기본값. 서버·UI 없이 CLI+YAML로 시작.
- **vs dbt (변환)**: SQL을 강요하지 않는다. map/steps 선언으로 변환의 90%를 커버하고, SQL·Python은 탈출구로 내려간다.
- **vs Airflow/Dagster (오케스트레이션)**: DAG 코드 없음. 데이터 준비 신호 기반 선언형 의존성. 멱등 재실행이 정상 동작.
- **vs Great Expectations (품질)**: 별도 프레임워크가 아니라 적재 직전 검증 게이트로 파이프라인에 내장.

## Databricks 미니 버전으로서의 대응 관계

DE Arsenal은 Databricks 데이터 제품군의 **저비용 고효율 미니 버전**을 지향한다. 그들의 기능 목록을 따라가는 게 아니라, 각 제품이 푸는 **문제**에 구조적으로 싼 해법을 대응시킨다. 비용 구조 비교는 [07-cost-efficiency.md](07-cost-efficiency.md) 참조.

| Databricks | 푸는 문제 | Arsenal 대응 | 시기 |
|---|---|---|---|
| Lakeflow Connect (100+ 커넥터) | 수집 | **Pugio** — 커넥터 수 대신 신뢰성 코어 + 낮은 커넥터 추가 비용 | P0 |
| Spark Declarative Pipelines | 선언형 변환 | **Gladius** — map/steps→SQL, DuckDB 벡터화 | P0 |
| Lakeflow Jobs | 오케스트레이션 | **Spatha** — 신호 기반 의존성, 멱등 재실행 | P0~P1 |
| DLT Expectations | 품질 게이트 | **Scutum** — 적재 직전 검증·DLQ·data contract | P0~P1 |
| Lakehouse 모니터링 / lineage | 관측성 | **Scorpio** — lineage·freshness·단위 비용 지표 | P1 |
| Predictive Optimization (OPTIMIZE/VACUUM) | 스토리지 유지보수 | **Onager** — 후보 자동 선정 compaction, 백필 격리 | P1 |
| Databricks SQL (DWH) | 쿼리·서빙 | **Gladius 쿼리 모드** — Parquet 레이크에 DuckDB 즉석 쿼리(미니 DWH) | P1 |
| Zerobus / Real-Time Mode | 스트리밍 | **Hasta** — 폴링 마이크로배치부터 정직하게 | P2 |
| (파트너 영역: Census/Hightouch) | reverse ETL | **Pilum** | P2 |
| 대규모 Spark 클러스터 | 분산 처리 | **Ballista** — Executor 백엔드 교체, 선언 불변 | P2 |
| Unity Catalog | 카탈로그·거버넌스 | **Aquila(신규 제안)** — 파일 기반 경량 카탈로그. Scorpio(lineage)·Scutum(contract)의 저장소를 묶는다 | P2 |
| LTAP 단일 스토리지 복사본 | 복사본 난립 방지 | **Arrow/Parquet 허브** — 모든 도구가 같은 파일을 읽는다. 도구 간 데이터 복사 없음 | P0 (구조) |
| Lakebase (서버리스 Postgres) | 트랜잭션 DB | **의도적 제외** — OLTP는 만들지 않는다. 기존 Postgres의 sink/source로 연결만 | — |
| Unity AI Gateway | AI 자산 거버넌스 | **의도적 제외** — 데이터 도구에 집중 | — |

미니 버전의 원칙: **그들의 기능 하나가 우리의 제품 하나와 대응할 필요는 없다.** 구조(오픈 포맷 단일 복사본, in-process 엔진, 선언형)가 같은 문제를 더 싸게 풀면 된다.

## 제품 철학 — 모든 판단의 기준

기능을 더할 때마다 "이게 넷을 다 지키나?"를 되묻는다. 하나를 위해 나머지를 희생하지 않는다.

1. **가볍게** — 작게 시작. 한 도구만 집어 바로 쓴다. 무거운 전제 인프라 강요 없음.
2. **빠르게** — 벌크·벡터화·병렬. 작다고 느리지 않다.
3. **정확하게** — 멱등·재개·검증이 기본값. 끊겨도 중복·누락 없음.
4. **누구나 쉽게** — 선언형(YAML + 가끔 SQL). 분석가도 바로 쓴다.

이 네 가지를 비용 언어로 번역하면 그대로 **저비용 고효율**이 된다: 가볍게=인프라 비용 0에서 시작, 빠르게=컴퓨트 시간 최소, 정확하게=재작업(재수집·재처리) 낭비 제거, 쉽게=엔지니어 병목 비용 제거. 비용은 부가 기능이 아니라 이 철학의 구조적 결과다 — 상세는 [07-cost-efficiency.md](07-cost-efficiency.md).

## 설계 원칙 (전 무기 공통)

1. **엔진은 빌린다, 만들지 않는다.** 벡터화(DuckDB)·분산(Spark/Flink)은 교체 가능한 백엔드 플러그인. 우리가 만드는 것은 신뢰성 코어와 선언형 인터페이스.
2. **재실행은 기본값, 예외가 아니다.** 모든 처리 단위(Unit of Work)는 결정적 ID로 멱등하게.
3. **사용자가 보는 것은 선언뿐.** 수집 루프·재시도·체크포인트·재개는 엔진이 흡수.
4. **상태는 절대 메모리에만 두지 않는다.** SQLite 영속 저장. 죽어도 살아남는 것이 신뢰성의 근거.
5. **쉬운 층을 위에, 강력한 층을 탈출구로.** map → steps → SQL → Python 순의 자연스러운 하강.
6. **코어는 작게, 가속·확장은 선택적으로.** 작게 시작하는 것이 코어의 불변 약속.
7. **각 도구는 독립하되 느슨하게 연결된다.** 작은 표준 인터페이스로 맞물리고, 어느 것도 빼거나 갈아끼울 수 있다 — 락인 없음.

## 핵심 사용자 시나리오 (P0 기준)

### 시나리오 A — 분석가의 첫 수집
```yaml
# github-issues.yaml
name: github-issues
source:
  type: rest
  url: https://api.github.com/repos/duckdb/duckdb/issues
  pagination: { mode: page, param: page, size: 100 }
  rate_limit: { rps: 5 }
sink:
  type: parquet
  path: ./data/issues
```
```bash
pugio run github-issues.yaml
```
서버도, DAG 코드도, 파이썬 스크립트도 없다. YAML 하나와 명령 하나.

### 시나리오 B — 새벽 3시에 끊긴 파이프라인
1,000페이지 중 612페이지에서 네트워크가 끊겼다. 담당자는 아침에 `pugio run`을 다시 실행한다(또는 재시도 정책이 자동으로). 엔진은 SQLite 상태를 읽고 **613페이지부터** 이어서 받는다. 이미 받은 612페이지는 결정적 파일명 덕에 중복 적재되지 않는다. 재개 비용 ≤ 진행 중이던 청크 1개.

### 시나리오 C — 토큰 만료
OAuth 토큰이 만료됐다. 이것은 에러가 아니라 **갱신 트리거**다. Auth Refresh Hook이 토큰을 갱신하고 그 지점부터 재개한다. 아무도 깨지 않는다.

### 시나리오 D — 분석가의 변환
```yaml
# transform.yaml
name: clean-issues
input: ./data/issues
steps:
  - filter: "state = 'open'"
  - rename: { created_at: opened_at }
  - cast: { number: bigint }
  - select: [number, title, opened_at, user]
output: ./data/issues_clean
```
엔진이 이 선언을 SQL로 컴파일해 DuckDB 벡터화 엔진에서 실행한다. 단일 노드에서 수백 GB까지 편안하다.

## 용어 사전

| 용어 | 정의 |
|---|---|
| **Unit of Work (Unit)** | 수집·처리의 최소 단위. 예: REST의 페이지 1개, 시간 윈도우 1개. 결정적 ID를 가진다 |
| **결정적 ID (deterministic ID)** | 같은 입력 파라미터 → 항상 같은 ID. `sha256(pipeline:source:unit_key)` 기반. 멱등성의 근거 |
| **StateStore** | SQLite 기반 영속 상태 저장소. unit 상태(pending/running/done/failed)와 커서를 기록 |
| **멱등 전략 (idempotency strategy)** | 끝점 능력별 중복 방지 방법. temp→MERGE(PG), partition swap(BQ), 결정적 파일명(객체 스토리지) |
| **검증 게이트 (validation gate)** | 적재 직전 스키마·null·범위·중복 규칙 검사. 위반 시 정책(차단·격리·경고) |
| **DLQ (Dead Letter Queue)** | N회 실패한 unit의 격리 보관소. 원인 해결 후 재투입 |
| **Arrow/Parquet 허브** | 도구 간 데이터 교환 표준. 모든 도구가 읽고 쓰는 공통 포맷 |
