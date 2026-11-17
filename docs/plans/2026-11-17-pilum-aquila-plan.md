# Pilum · Aquila 착수 계획 (0.5 / 0.6)

작성일: 2026-11-17
상태: **승인 대기 설계안**
선행 조건: [P2 완주 계획](2026-11-17-p2-completion-plan.md)의 0.4.0 출시 (특히 StateStore v2 migration 프레임워크와 `OutputProof`)

---

## 0. 결론

| | Aquila (카탈로그·거버넌스) | Pilum (reverse ETL·디스패치) |
|---|---|---|
| 한 줄 정의 | 레포 안의 모든 데이터셋을 **파일 하나로 찾고, 스키마 이력·lineage·계약을 조회**하는 경량 카탈로그 | 레이크의 결과를 **외부 끝점(webhook·HTTP API·SaaS)으로 중복 없이 발사**하는 디스패처 |
| 대응 | Unity Catalog의 미니판 | Census/Hightouch의 미니판 |
| 위험 | 낮음 — 로컬 읽기 전용, 외부 부작용 없음 | 높음 — 외부로 나가는 부작용, 되돌릴 수 없음 |
| 기반 재사용 | `arsenal_core.schema`, StateStore lineage, `OutputProof`, Scutum `DataContract`, Augur `Catalog` | Pugio retry·auth refresh·`TokenBucket`, Scutum DLQ 정책, StateStore v2 migration, Hasta outbox(이벤트 모드) |
| 규모 추정 | 6~9일 | 8~12일 |
| 목표 버전 | **0.5.0** | **0.6.0** |

**Aquila를 먼저 한다.** 이유 셋:

1. 부작용이 없어 작게 증명하기 쉽다. Pilum은 외부로 나가는 첫 무기라 실수의 비용이 크다.
2. 0.4에서 생긴 `OutputProof`(schema hash, row count, input versions, plan hash)가
   이미 lineage의 완벽한 원천이다. 지금 묶지 않으면 증거가 각 state DB에 흩어진 채 남는다.
3. Pilum이 "무엇을 보낼지" 고를 때 Aquila 조회(데이터셋·계약·소유자)를 그대로 쓴다.

둘은 코드상 독립이므로 인력이 있으면 병렬도 가능하다. 단, 같은 StateStore migration 번호를
쓰지 않도록 Aquila는 **별도 DB 파일**(`aquila.db`)을 쓰고, Pilum만 StateStore v4 migration을 가진다.

---

## 1. Aquila — 카탈로그·거버넌스

### 1.1 해결하는 문제

- **데이터셋이 어디 있는지 모른다** — Pugio·Gladius·Hasta·Ballista 출력이 YAML마다 흩어져 있다.
- **스키마가 언제 바뀌었는지 모른다** — 스냅샷 비교는 Pugio 실행 순간에만 일어나고 이력이 없다.
- **lineage가 파이프라인별 state DB에 갇혀 있다** — 여러 파이프라인을 가로지르는 그래프가 없다.
- **계약·소유자·민감도 정보가 없다** — 누가 책임지고, PII가 있는지 알 수 없다.

### 1.2 제품 경계

**한다**
- SQLite 파일 하나(`aquila.db`)로 시작, 서버·데몬 없음
- 데이터셋 레지스트리: 이름, URI, 포맷, 소유자, 태그, 설명, 상태(active/deprecated)
- 스키마 버전 레지스트리: 버전별 스냅샷 + `compare_schema_snapshots()` diff
- lineage 그래프: 데이터셋 단위 상·하류 (컬럼 단위는 v2)
- 계약 인덱스: Scutum `DataContract` 파일 ↔ 데이터셋 연결
- 거버넌스 태그: `pii`, `owner`, `tier` 같은 분류 (선언만, 강제는 안 함)
- 전문 검색: SQLite FTS5

**하지 않는다 (v1)**
- 접근 제어·권한 강제 — 서버 없이 강제할 수 없다. 정직하게 `08-limits.md`에 명시
- 실시간 push 이벤트 버스 — v1은 **pull(scan)** 모델
- 외부 카탈로그(Unity·Glue·Hive Metastore) 동기화 — v2 이후 어댑터
- Iceberg REST catalog 대체 — Iceberg catalog는 Ballista가 쓰는 별개 계층

### 1.3 핵심 설계 결정

| ADR | 결정 | 근거 |
|---|---|---|
| AQ-ADR-1 | **pull 모델**: `aquila scan`이 state DB·`OutputProof`·YAML·contract 파일을 읽어 등록 | 다른 무기가 Aquila에 의존하지 않는다 (독립성 원칙). Aquila를 빼도 아무것도 안 깨진다 |
| AQ-ADR-2 | 별도 `aquila.db` 파일, 파이프라인 StateStore와 분리 | 파이프라인 상태의 single-writer 불변식(P2-ADR-7)을 건드리지 않는다 |
| AQ-ADR-3 | 스캔은 멱등 — 같은 입력 재스캔 시 버전·간선이 늘지 않는다 | 결정적 ID 원칙. 스키마 버전 키 = `schema_hash` |
| AQ-ADR-4 | **Augur와의 경계**: Aquila가 system of record, Augur `Catalog`는 검색용 뷰. Augur는 `aquila.db`가 있으면 읽고, 없으면 기존 `build_catalog()` 유지 | Augur의 독립 설치를 깨지 않으면서 중복 진실 원천을 없앤다 |

### 1.4 데이터 모델 (초안)

```text
datasets(dataset_id PK, name UNIQUE, uri, format, owner, status, description, created_at, updated_at)
dataset_tags(dataset_id, key, value)                      -- pii=true, tier=gold
schema_versions(dataset_id, schema_hash, snapshot_json, first_seen_at, last_seen_at,
                PK(dataset_id, schema_hash))
lineage_edges(upstream_id, downstream_id, via, revision, plan_hash, observed_at,
              PK(upstream_id, downstream_id, via))
contracts(dataset_id, contract_path, contract_hash, last_checked_at)
scan_sources(source_path PK, kind, content_hash, scanned_at)  -- 재스캔 생략용
datasets_fts USING fts5(name, description, tags, columns)
```

`dataset_id` = `canonical_hash(normalized_uri)` — URI가 같으면 어느 도구가 등록해도 같은 데이터셋.

### 1.5 CLI

```bash
aquila scan [PATH ...]            # YAML·state DB·proof·contract 수집 (멱등, --dry-run)
aquila search "orders pii"        # FTS
aquila show orders                # 메타 + 최신 스키마 + 계약 + 상·하류 1단계
aquila history orders             # 스키마 버전 타임라인 + diff
aquila lineage orders --depth 3 --direction up|down|both [--format json|mermaid]
aquila tag orders pii=true owner=data-team
aquila deprecate orders --reason "replaced by orders_v2"
```

### 1.6 단계와 DoD

| 단계 | 내용 | DoD |
|---|---|---|
| **AQ-0** 설계 고정 (0.5일) | 이 문서 승인, ADR 4건, `de-aquila` 패키지 골격 | CI green, `pip install de-aquila`가 다른 무기를 끌어오지 않음 |
| **AQ-1** 레지스트리 + 스키마 이력 (2~3일) | 데이터 모델, migration, Pugio/Gladius YAML 스캐너, `show`/`history` | 같은 레포 재스캔 시 행 수 불변 테스트, 스키마 변경 fixture에서 diff 정확 |
| **AQ-2** lineage (1.5~2일) | StateStore lineage + `OutputProof.input_versions` → 간선, `lineage` CLI, mermaid 출력 | 3단 파이프라인 fixture(pugio→gladius→ballista)에서 상·하류 그래프 정확, 순환 감지 |
| **AQ-3** 계약·거버넌스·검색 (1.5~2일) | Scutum contract 연결, 태그, deprecate, FTS5 | contract 해시 변경 감지, PII 태그 검색, deprecated 데이터셋을 쓰는 하류 경고 |
| **AQ-4** 통합 (1~2일) | Augur 어댑터, `arsenal status`에 카탈로그 요약, Scorpio freshness 연결, Hasta/Ballista 스캐너 | Augur가 `aquila.db` 유무 양쪽에서 기존 eval 34 case 결과 불변 |

### 1.7 리스크

| 리스크 | 대응 |
|---|---|
| URI 정규화 불일치로 같은 데이터셋이 둘로 등록 | 정규화 규칙(스킴 소문자, 끝 `/` 제거, glob 접미사 제거)을 property test로 고정 |
| 큰 레포 스캔이 느림 | `scan_sources.content_hash`로 바뀐 파일만 재처리 |
| Augur와 진실 원천 이중화 | AQ-ADR-4: Augur는 읽기만, 쓰기는 Aquila만 |

---

## 2. Pilum — reverse ETL·디스패치

### 2.1 해결하는 문제

- 가공된 결과를 CRM·알림·webhook으로 되돌려 보내려면 별도 도구가 필요하다.
- 재실행하면 **같은 레코드를 두 번 발사**한다 (중복 이메일, 중복 CRM 업데이트).
- 발사 실패가 **조용히 누락**된다.
- 바뀐 것만 보내지 못해 매번 전량 발사 → API rate limit·비용 낭비.

### 2.2 제품 경계

**한다**
- 소스: Parquet 데이터셋 또는 Gladius 쿼리 결과 (Arrow 허브 그대로)
- **변경분만 발사**: 레코드 키별 content hash를 원장(ledger)에 저장하고 diff
- 모드: `insert`(새 키만), `upsert`(새 키 + 바뀐 키), `mirror`(+ 사라진 키는 delete 호출)
- 끝점 v1: generic HTTP(JSON body 템플릿), webhook, Slack incoming webhook
- 멱등 키: 끝점이 지원하면 `Idempotency-Key` 헤더로 전달
- 배치 발사(끝점이 bulk API를 가지면), rate limit, retry/backoff, auth refresh
- DLQ: N회 실패 레코드는 격리, `pilum replay`로 재투입
- **dry-run diff preview**: 무엇이 몇 건 나갈지 실제 발사 전에 보여 준다

**하지 않는다 (v1)**
- exactly-once 보장 — 끝점이 멱등 키를 존중하지 않으면 at-least-once가 상한. 정직하게 표기
- 이름 있는 SaaS 커넥터(HubSpot·Salesforce) — generic HTTP로 증명 후 v2에서 템플릿으로
- 양방향 동기화, 충돌 해결
- 스트리밍 발사 — v1은 배치. 이벤트 모드는 Hasta outbox 연동으로 v2

### 2.3 핵심 설계 결정

| ADR | 결정 | 근거 |
|---|---|---|
| PL-ADR-1 | **dispatch_id = hash(pipeline, destination, record_key, content_hash)** | 같은 내용의 같은 레코드는 몇 번 재실행해도 같은 ID → 원장에서 skip, 끝점엔 같은 멱등 키 |
| PL-ADR-2 | **원장 기록은 끝점 성공 응답 후에만** (I2 불변식과 동일) | 발사 전 기록하면 실패 시 영구 누락. 발사 후 기록이라 크래시 시 재발사 → 멱등 키가 흡수 |
| PL-ADR-3 | 원장은 StateStore **v4 migration**(`dispatch_ledger`, `dispatch_dlq`) | 0.4의 forward migration·backup 프레임워크 재사용, 별도 저장소 발명 안 함 |
| PL-ADR-4 | 기본 모드는 `upsert`, `mirror`(delete)는 명시 opt-in + 확인 | delete 발사는 되돌릴 수 없다 |
| PL-ADR-5 | 최초 실행 안전장치: 원장이 비어 있고 대상 건수가 `max_first_dispatch`(기본 1,000) 초과면 거부 | 실수로 전 고객에게 발사하는 사고 방지. `--confirm-initial-load`로 해제 |

### 2.4 YAML (초안)

```yaml
pipeline: churn-alerts
source:
  type: parquet            # 또는 query: "SELECT ... FROM read_parquet(...)"
  path: data/marts/churn_risk/
key: [customer_id]
mode: upsert               # insert | upsert | mirror
destination:
  type: http
  url: https://api.example.com/v1/alerts
  method: POST
  auth: { type: bearer, token_env: ALERTS_TOKEN }
  body: |                  # Jinja-less 단순 필드 매핑 (map 문법 재사용)
    { "id": "{customer_id}", "score": "{risk_score}" }
  idempotency_header: Idempotency-Key
  batch_size: 1            # 끝점이 bulk를 지원하면 >1
  rate_limit: { rps: 5 }
retry: { max_attempts: 5, backoff: exponential }
dlq: { after_attempts: 5 }
safety: { max_first_dispatch: 1000 }
```

### 2.5 CLI

```bash
pilum plan  churn.yaml            # dry-run: insert N / update M / delete K / unchanged U (+ 샘플)
pilum run   churn.yaml [--confirm-initial-load]
pilum status churn.yaml           # 원장 크기, 마지막 성공, DLQ 건수
pilum replay churn.yaml [--limit N]   # DLQ 재투입
pilum reset churn.yaml --confirm  # 원장 초기화 (다음 run이 전량 재발사)
```

### 2.6 단계와 DoD

| 단계 | 내용 | DoD |
|---|---|---|
| **PL-0** 설계 고정 (0.5일) | 이 문서 승인, ADR 5건, `de-pilum` 골격 | CI green, 독립 설치 |
| **PL-1** 원장·diff 코어 (2~3일) | StateStore v4 migration, `DispatchSpec`, key/content hash, insert/upsert/mirror diff, `pilum plan` | v0.4 state DB(v3)가 v4로 무손실 migration, diff property test(재실행 시 발사 0건) |
| **PL-2** HTTP 끝점 (2~3일) | generic HTTP·webhook·Slack, 멱등 헤더, batch, rate limit, retry, auth refresh | fake HTTP 서버로 크래시 매트릭스: 발사 후·원장 기록 전 크래시 → 재실행 시 같은 멱등 키로 1회만 반영 |
| **PL-3** 안전장치·DLQ (1.5~2일) | DLQ, `replay`, `max_first_dispatch`, mirror 확인, secret 마스킹 | 4xx(영구)·5xx/429(재시도) 분류 테스트, 로그·오류에 토큰 미노출 테스트 |
| **PL-4** 통합 (1.5~2일) | `arsenal run` dispatch, Scorpio 메트릭(발사 수·실패율·지연), Spatha 태스크, Aquila에 하류 끝점 등록 | gladius → pilum 레시피 E2E, Scorpio에서 DLQ 건수 확인 |
| **PL-5** (선택, 0.7) | Hasta outbox 이벤트 모드, HubSpot/Salesforce 템플릿 | — |

### 2.7 리스크

| 리스크 | 대응 |
|---|---|
| 잘못된 설정으로 대량 오발사 | PL-ADR-5 최초 실행 상한 + `pilum plan` 필수 권장 + mirror opt-in |
| 끝점이 멱등 키를 무시 → 크래시 시 중복 | 문서에 at-least-once 명시, 끝점 능력 선언(`supports_idempotency_key`)을 status에 표시 |
| 원장과 실제 끝점 상태 불일치(외부에서 수정) | v1은 원장이 진실 원천임을 명시, `reset`으로 재동기화 |
| content hash가 부동소수점 표현에 흔들림 | `canonical_value()` 재사용 (0.4에서 이미 결정적 정규화 보유) |

---

## 3. 전체 타임라인

```
0.4.0  P2 완주 (Phase 1~4)                       9~14일
  ↓
0.5.0  Aquila  AQ-0 → AQ-1 → AQ-2 → AQ-3 → AQ-4   6~9일
  ↓    (병렬 가능하면 PL-0~PL-1을 여기서 시작)
0.6.0  Pilum   PL-0 → PL-1 → PL-2 → PL-3 → PL-4   8~12일
```

## 4. 착수 전 결정이 필요한 것

1. **Aquila 먼저, Pilum 다음** 순서 승인 (또는 병렬)
2. **AQ-ADR-4** — Augur가 Aquila를 선택적으로 읽는 경계 승인
3. **PL-ADR-5** — 최초 발사 상한 기본값 1,000건이 적절한지
4. Pilum v1 끝점 범위를 generic HTTP·webhook·Slack 셋으로 한정하는 것 승인
