# 09. 오픈소스 차용 전략 (OSS Leverage)

> 원칙 1("엔진은 빌린다")의 실행 지도. 이기는 오픈소스는 기존 도구를 대체하지 않고 **사람들이 이미 신뢰하는 엔진 위에 빠진 층을 채운다** — dbt가 SQL 위에 그랬듯. 우리의 빠진 층은 DuckDB 생태계의 "신뢰성 있는 글루"다.

## 우리가 소유하는 코드는 4가지뿐

1. **unit-of-work + 결정적 ID + StateStore** — 엔진이 무엇이든 exactly-once 결과를 보장하는 계약
2. **YAML-only UX와 원클릭 레시피** — 분석가의 진입로
3. **검증 게이트 + DLQ 기본값** — 오염 데이터 차단
4. **`arsenal` 글루** — 위 셋을 묶는 단일 진입점

나머지는 전부 빌린다. 아래 표에 없는 것을 직접 만들자는 제안은 이 문서를 먼저 반박해야 한다.

## 차용 지도

| 능력 | 차용 | 우리가 얹는 것 | 시기 |
|---|---|---|---|
| OLAP 엔진 | DuckDB | steps→SQL 컴파일, 즉석 쿼리 | M3 |
| **DB 소스 (PG/MySQL/SQLite)** | DuckDB `postgres_scanner`/`mysql_scanner`/ATTACH | 키 범위 unit 분할 + 재개·멱등 | **M2** |
| S3/GCS/R2 읽기·쓰기 | DuckDB `httpfs` | 결정적 키 멱등 쓰기 | P1 |
| 커넥터 롱테일 (100+) | **dlt verified sources 래핑** (`type: dlt`) | Source 프로토콜 어댑터 — dlt 리소스를 unit 계약으로 | P1 |
| SQL 변환 생태계 | **dbt-duckdb 인터롭** | `arsenal.yaml`의 `- dbt: ./project` 한 단계 | P1 |
| 커스텀 수집 | 사용자의 Python (`type: python`) | Source 프로토콜 로더 — 탈출구 | **M2** |
| HTTP/재시도/CLI/스펙 | httpx / tenacity / typer / pydantic | — (이미 차용) | M1 |
| 엑셀 파싱 | fastexcel (Arrow 네이티브) | optional extra | M2 |
| 테이블 포맷 | duckdb-iceberg | — | P2 |
| BI/시각화 | Metabase, evidence, Rill | 안 만든다 — 레시피가 연결 안내 | — |

## 전략적 수 세 가지

### 수 1 (M2): DuckDB를 깊게 — DB 소스가 공짜로 생긴다

DE의 1번 수집 작업은 REST가 아니라 "운영 DB → 웨어하우스 동기화"다. DuckDB scanner로 드라이버·타입 매핑·커서를 전부 위임하고, 우리는 unit 분할과 상태만 얹는다:

```yaml
source:
  type: database
  dialect: postgres          # postgres | mysql | sqlite
  dsn_env: PROD_DB_DSN
  table: orders
  split: { key: id, chunk: 100000 }   # 키 범위 = unit → 재개·병렬·멱등이 기존 코어로
```

### 수 2 (P1): dlt는 경쟁자가 아니라 커넥터 창고

커넥터 백화점 경쟁은 하지 않는다 — 대신 dlt의 검증된 소스 수백 개를 `type: dlt`로 래핑해 커버리지를 흡수한다. dlt 사용자에겐 "내 소스를 YAML+멱등 계약으로 돌려주는 도구"가 된다. 구현은 M2의 Python 탈출구와 같은 메커니즘(Source 프로토콜 어댑터).

### 수 3 (P1): dbt와 싸우지 않고 실어준다

map/steps는 분석가용 쉬운 층으로 유지하고, `arsenal.yaml`이 dbt 프로젝트를 실행 단계로 받는다:

```yaml
transforms:
  - transform.yaml           # 우리 steps
  - dbt: ./dbt_project       # dbt-duckdb로 실행 — 기존 자산 그대로
```

기존 dbt 팀에게 Arsenal은 "dbt를 버리는 도구"가 아니라 "dbt 앞단(수집·검증)을 해결하는 도구"가 된다. 하강 경로 완성: map → steps → SQL/dbt → Python.

## 하지 말 것

- **dlt/dbt 하드 의존성** — optional extra(`de-arsenal[dlt]`, `[dbt]`)로만. 코어는 가볍게.
- **자체 커넥터 양산** — 수 1·2가 있는데 손으로 커넥터를 짜는 건 시간 낭비.
- **BI/시각화 제작** — 연결만.
- **포크** — 업스트림에 이슈/PR로 기여하고, 우리 저장소엔 어댑터만 둔다.

## 채택 경로 (누가 어디로 들어오나)

| 사용자 | 진입로 | 버릴 필요 없는 것 |
|---|---|---|
| 분석가 | 레시피 + YAML | 엑셀·기존 워크플로 |
| 1인 DE | `database` 소스 + cron | 운영 DB, 서버 한 대 |
| dbt 팀 | dbt 인터롭 | dbt 프로젝트 전체 |
| dlt 사용자 | `type: dlt` | 만들어둔 소스 |
| 엔지니어 | Python 탈출구 | 자기 코드 |

"모두가 쓸만한" = 각자의 진입로가 있고 **아무도 자기 것을 버릴 필요가 없는 것**.

## 쓸만함 판정 기준 (v0.1 성공 조건)

- 우리 자신이 실제 반복 업무 1개를 M1 완료 직후부터 매주 이 도구로 돌린다 (도그푸딩 게이트)
- 시키지 않은 타인 1명이 두 번 이상 자발적으로 쓴다
- 실전 API 5종(GitHub Link 헤더, Stripe cursor, 공공데이터포털 euc-kr, Notion rate limit 등)이 YAML만으로 표현된다 — 안 되면 스펙을 고치거나 탈출구로 해결됨을 확인

이 셋 중 하나라도 실패하면 v0.1은 실패로 간주하고 원인을 고친다.
