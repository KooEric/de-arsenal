# 08. 정직한 한계선 (Honest Limits)

> "가볍다"는 주장은 측정으로 증명하고, "안 된다"는 것은 숨기지 않고 문서화한다
> ([07-cost-efficiency.md](07-cost-efficiency.md) 비용 철학 4와 동일 원칙, Ballista
> 문제 2의 P0 몫). 이 문서는 Arsenal이 **지금 무엇을 보장하고, 무엇을 보장하지
> 않는지**를 한 곳에 모은다.

## 단일 노드 처리 envelope

Arsenal(Pugio/Gladius)은 분산 엔진을 만들지 않는다([설계 원칙 1](roadmap.md)) —
빌린 엔진(DuckDB) 위에서 단일 노드 벡터화가 커버하는 구간을 겨냥한다. 아래
수치는 [`benchmarks/RESULTS.md`](../benchmarks/RESULTS.md)의 실측 1회 실행 결과다.

| 항목 | 값 |
|---|---|
| 입력 규모 | ~1.01 GB (13,000,000 rows, Parquet) |
| 변환 | `filter` + `cast` + `select` (scenario-D 스타일) |
| 중앙값 소요 시간 | 0.105s (3회 실행) |
| 처리량 | ~123.9M rows/sec |
| 측정 머신 | Apple M3 Pro (12 cores), 36GB RAM, macOS, 로컬 NVMe |

**이 숫자를 어떻게 읽어야 하는가:**

- **같은 머신 회귀 기준선이지, 크로스 머신 보증이 아니다.** "이 규모 → 이 시간"은
  이 저장소의 CI/개발 머신에서 "이 변경이 N배 느려졌는가"를 판단하기 위한
  것이지, 사용자 환경에서의 성능 SLA가 아니다.
- **웜 캐시 측정이다.** 입력 Parquet을 타이밍 직전 같은 프로세스에서 생성했기
  때문에 OS 페이지 캐시에 이미 올라가 있을 가능성이 높다 — 콜드 디스크 I/O를
  측정한 것이 아니다.
- **컬럼 프로젝션 pushdown이 적용된다.** DuckDB의 Parquet 리더가 실제로 쓰이는
  컬럼만 읽고(5개 중 4개), `filter`가 행의 절반 가까이를 먼저 쳐낸다 — 전체
  1GB가 메모리에 그대로 올라간 적 없는 실행이다.
- 12 코어 병렬 실행(`PRAGMA threads`) 결과다.
- **재실행할 때마다 갱신할 숫자다.** `gladius.engine.run_transform`이나
  트랜스파일러가 바뀌면 `benchmarks/bench_transform.py`를 다시 돌리고
  `RESULTS.md`를 갱신한다 — 오래된 벤치마크는 신뢰할 수 없는 벤치마크다.

## 지원하지 않는 것 (의도된 범위 밖)

아래는 버그가 아니라 **설계 선택**이다. 언제 다시 볼지는 [roadmap.md](roadmap.md) ·
[01-scope.md](01-scope.md)의 P1/P2 표에 이미 정해져 있다.

| 항목 | 우선순위 | 왜 지금은 아닌가 |
|---|---|---|
| 밀리초급 실시간 스트리밍 | 범위 밖 | Hasta(0.4)는 폴링 마이크로배치(초~분)다. Kafka·binlog/WAL 등 log 기반 CDC, Flink식 상태 연산·윈도 조인, exactly-once 외부 전송은 하지 않는다 — 아래 [Hasta 절](#hasta-스트리밍의-보장과-한계-04) 참고. 밀리초급은 전용 인프라 없이는 불가하다([07-cost-efficiency.md](07-cost-efficiency.md) "줄여주지 못하는 비용"). |
| 범용 분산 실행 | 범위 밖 | Ballista(0.4)는 portable Gladius 플랜 하나를 Spark on Kubernetes에서 실행하고 Iceberg로만 커밋한다. raw Parquet 원격 출력의 원자성, 임의 Spark 코드·raw SQL·Python UDF의 분산 실행, 클러스터 프로비저닝은 하지 않으며 그 인프라 비용은 사용자가 진다 — 아래 [Ballista 절](#ballista-분산-실행의-보장과-한계-04) 참고. |
| 스키마 드리프트 감지+정책 | P1 | `schema_drift`로 `allow`/`warn`/`block`을 선택한다. `warn`은 경고 후 최신 스냅샷을 기록하고, `block`은 해당 unit을 failed 처리해 적재를 막는다. 현재는 첫 non-empty batch의 필드명·타입·nullable 비교만 지원한다. |
| 증분 변환 (`incremental: by_unit/by_key`) | P1 | 입력 파일 시그니처와 변환 스펙 해시를 SQLite에 기록한다. 신규 파일은 `by_unit`에서 append하고 `by_key`에서는 신규 결과가 기존 키를 덮어쓴다. 기존 파일 변경·삭제 또는 스펙 변경은 안전성을 위해 전체 재계산한다. `by_key`는 소스에서 삭제된 행을 추론하지 않는다. |
| Python UDF step | P1 | `steps`의 `python`, `args`, `output`으로 `module:function`을 등록하고 SQL 호출로 컴파일한다. 실행 환경에는 `de-gladius[python]`(numpy)가 필요하며, 함수의 부작용·분산 실행·벡터화는 보장하지 않는다. |
| `pugio status --cost` | P1 | 완료된 unit의 누적 행 수·바이트·처리 시간과 마지막 완료 시각을 보여준다. `--max-age-seconds`를 함께 주면 freshness 상태와 stale alert를 출력한다. 단가 환산·외부 알림 전송·분산 집계는 P2 범위다. |
| lineage | P1 | Pugio run 시작 시 source type/ref와 sink type/ref를 상태 DB에 최신 edge로 기록한다. column-level lineage·알림·다중 파이프라인 graph 조회는 아직 없다. |
| Onager small-file compaction/backfill | P1 | `onager compact`가 로컬 Parquet 디렉터리의 target 크기 미만 파일을 자동 선정하고 `--dry-run`, `--max-input-bytes`, JSONL 실행 로그를 제공한다. Scutum file lock의 bounded retry/backoff와 성공 전 원본 backup을 사용하며, `run_backfill`은 snapshot workspace에서 실행되고 explicit `promote_backfill` 전에는 활성 dataset을 바꾸지 않는다. S3/GCS URI는 지원하지 않는다. |
| Spatha workflow | P1 | `Workflow`가 중복·미존재 dependency와 cycle을 검증하고, 준비 신호·실행 window·priority를 반영한 `ready_tasks`/`run_ready`와 task lock retry를 제공한다. 외부 신호 감시 데몬은 없다. |
| Scutum data contract | P1 | `PipelineSpec.contract`가 `DataContract.check(RecordBatch)`와 연결되어 필드 존재·Arrow 타입·실제 null 값·추가 필드를 검사하고 block/quarantine/warn 정책을 적용한다. `FileLock`은 lock 충돌 retry/backoff를 제공한다. |
| dlt/dbt 인터롭 | P1 | `type: dlt` 래퍼와 `arsenal.yaml`의 `- dbt: ./project` 단계가 optional extra로 제공된다. dlt는 소스 함수·리소스의 인증/페이지네이션을 사용하고, dbt 단계는 `dbt run`만 호출한다(`deps`/`test`/`build` orchestration은 아직 없음). |
| S3/GCS sink | P1 | `sink.type: parquet`의 `s3://`, `gcs://`, `gs://` 경로를 DuckDB `httpfs`로 쓸 수 있다. unit ID 기반 결정적 객체 키로 멱등성을 보장하지만, 원격 객체 교체의 cross-provider 원자성은 보장하지 않는다. |

## sink별 멱등 보장 범위

Scutum 설계 원칙("끝점 능력별 멱등 전략을 명시", [roadmap.md](roadmap.md) Scutum
문제 3)의 실제 구현 상태다.

| sink | 멱등 단위 | 전략 | 구현 |
|---|---|---|---|
| **parquet** | 파일 단위 | 결정적 파일명(`{unit_id}.parquet`) + `os.replace`로 원자 교체. 같은 unit은 같은 파일 → 재실행 = 덮어쓰기 = 중복 없음. | [`sinks/parquet.py`](../packages/pugio/src/pugio/sinks/parquet.py) |
| **duckdb** | 키 단위 upsert | `merge_key`로 식별되는 기존 행을 지우고 batch를 한 트랜잭션 안에서 delete+insert. in-batch 중복 `merge_key`는 `__arsenal_seq` 합성 컬럼 + `QUALIFY row_number() ... = 1`로 batch 내 마지막 값만 남긴다(postgres의 `ON CONFLICT` last-wins와 동일 결과를 내기 위함). | [`sinks/duckdb.py`](../packages/pugio/src/pugio/sinks/duckdb.py) |
| **postgres** | 키 단위 upsert | `merge_key`를 `PRIMARY KEY`로 둔 테이블에 `INSERT ... ON CONFLICT (merge_key) DO UPDATE SET ...`. 컬럼 전체가 merge_key면 `DO NOTHING`. | [`sinks/postgres.py`](../packages/pugio/src/pugio/sinks/postgres.py) |

### 알려진 한계 (Known Limitations)

- **postgres sink는 optional extra(`psycopg`)가 필요하다.** `de-arsenal`/`pugio`
  기본 설치(`uv tool install de-arsenal` / `pip install pugio`)에는 psycopg가
  포함되지 않는다 — `postgres` extra를 명시적으로 설치해야 한다
  (`uv tool install "de-arsenal[postgres]"` 또는 `pip install "pugio[postgres]"`).
  extra 없이 `build_sink`에 `sink: postgres` spec을 넘기면 `pugio.sinks`가
  `ModuleNotFoundError`를 잡아 `FatalError`("postgres sink requires the
  'postgres' extra: pip install pugio[postgres]")로 번역한다 — `api-to-postgres`
  레시피를 extra 없이 그대로 `arsenal run`하면 원문 traceback이 아니라 이 명확한
  에러 메시지로 즉시 중단된다.
- **`pugio dlq retry`는 REST `cursor`/`link` 페이지네이션을 지원하지 않는다
  (P0 스코프 락).** 프론티어 커서는 앞으로만 전진하므로, 격리된 과거 페이지의
  `unit_key`(`cursor=<val>`)는 재실행이 다시 생성할 수 없다 — requeue해도
  다음 run이 그 unit을 절대 재등록하지 않아 영원히 고아가 된다. `dlq retry`는
  이 조합을 명시적으로 거부하고 evidence(DLQ 파일)를 보존한다
  (`packages/pugio/src/pugio/cli.py`의 `dlq_retry`). `offset`/`page`/`file`/
  `database`/`python` 소스는 `unit_key`가 결정적으로 재생산되므로 정상 재시도된다.
- **DuckDB scanner 경로(postgres/mysql `DatabaseSource`)의 인증 실패가 일시적
  오류로 오분류될 수 있다.** `_raise_classified_duckdb`는 `duckdb.IOException` /
  `ConnectionException` / `TransactionException`을 전부 `RetryableError`로
  분류하는데, ATTACH 단계에서 잘못된 비밀번호로 인한 실패도 DuckDB가 이 예외
  타입 중 하나로 표면화할 수 있다 — 이 경우 영구적인 자격증명 오류(재시도해도
  절대 성공하지 않음)가 재시도 가능으로 잘못 분류된다. Postgres 직접 연결
  sink(`sinks/postgres.py`)는 sqlstate `28xxx`(인증)를 별도로 `FatalError`로
  가려내지만, scanner 경로는 이 세분화를 아직 갖고 있지 않다(테스트:
  `packages/pugio/tests/test_database_source_scanner.py::test_scanner_error_redacts_dsn`은
  DSN 마스킹만 검증하고 Fatal/Retryable 분류는 검증하지 않는다). 재시도
  백오프가 소진될 때까지 불필요하게 재시도된다는 뜻이며, 결과가 틀리게
  나오지는 않는다.

## Hasta 스트리밍의 보장과 한계 (0.4)

| 항목 | 보장하는 것 | 보장하지 않는 것 |
|---|---|---|
| 전달 | batch를 sink에 먼저 쓰고 lease로 fence된 체크포인트를 그다음에 전진시킨다. 어느 시점에 죽어도 커밋 안 된 batch 하나만 **같은 identity**로 재생되고 sink가 멱등하게 쓴다 — 12번 죽는 러너로 10만 이벤트가 정확히 한 번 도착(`packages/hasta/tests/test_resume_100k.py`) | 소스가 같은 cursor에 다른 이벤트를 돌려주는 경우. 이벤트 id·cursor가 안정적이어야 한다 |
| CDC | Postgres outbox의 `I`/`U`/`D` envelope를 `sequence` 순으로 DuckDB/Postgres에 트랜잭션으로 적용, event-id ledger로 재생 안전 | Kafka, binlog/WAL 등 log 기반 CDC. table cursor 선언은 hard delete를 볼 수 없어 CDC로 취급하지 않는다 |
| 동시성 | 스트림당 러너 하나(SQLite lease). Onager compaction은 lease를 잡고서만 실행되고 반쯤 커밋된 batch가 있으면 거부한다 | 여러 호스트에 걸친 lease, 데몬 감독·자동 재시작 |
| 종료·재처리 | `SIGINT`/`SIGTERM`은 진행 중 batch를 커밋하고 멈춘다. `checkpoint reset --confirm`은 새 generation으로 재생하고 ledger를 감사용으로 남긴다 | Parquet sink는 append라 reset 재생분이 기존 파일 옆에 쌓인다 — 원치 않으면 출력부터 비운다 |
| 끝없는 스트림 | `cursor.on_null: last_event_id`면 null cursor를 "따라잡음"으로 보고 마지막으로 커밋한 이벤트 id 다음부터 계속 폴링한다. 빈 폴링은 같은 ledger 행 하나로 합쳐진다 | 기본값(`end`)에서는 null cursor가 스트림을 영구 종료한다(`_hasta_exhausted`). `last_event_id`는 API가 이벤트 id를 cursor로 받아야 동작한다 |
| 처리량 | 같은 머신 회귀 기준 ~334k events/s(in-process, 네트워크 제외, [`benchmarks/RESULTS.md`](../benchmarks/RESULTS.md)) | 실제 REST 소스 처리량 — 네트워크 지연이 지배한다 |

**자격증명:** REST `headers`의 토큰은 `${ENV_VAR}`로 넣는다 — 스펙을 읽을 때 치환되므로
YAML에 비밀을 둘 필요가 없다. header *값*은 스트림 revision에 들어가지 않아 토큰을
교체해도 batch identity가 바뀌지 않는다(커밋 전 batch가 다른 identity로 두 번 쓰이지 않는다).
오류 메시지에는 URL의 비밀번호·쿼리 값이 마스킹되고 header 값은 나오지 않는다. URL 쿼리에 토큰을 넣으면 소스 식별자
(`rest_poll:<url>`)로 상태 DB에 저장되므로 header로 보내라. Outbox는 `dsn_env`로
DSN을 환경변수에서만 읽고, 오류 메시지에서 DSN을 마스킹한다.

## Ballista 분산 실행의 보장과 한계 (0.4)

Ballista는 **Iceberg 출력만** 원자적 production 결과로 인정한다. raw Parquet 출력은
preflight에서 거부된다.

| 항목 | 보장하는 것 | 보장하지 않는 것 |
|---|---|---|
| 결과 커밋 | 논리 run 하나당 run id로 태그된 Iceberg 스냅샷 하나. 재시도·다음 attempt는 기존 스냅샷을 재사용하고 다시 쓰지 않는다 | 전체 overwrite는 Iceberg 낙관적 동시성으로 충돌이 검출되지 않는다. "더 새로운 run이 이미 게시했는가" 검사와 커밋 사이의 짧은 창은 닫히지 않는다 |
| 입력 버전 | Iceberg 입력은 기록된 snapshot id로 읽는다. Parquet 입력은 목록 fingerprint(경로·크기·ETag)를 worker가 재계산해 다르면 실행을 거부한다 | Parquet fingerprint 확인과 Spark의 실제 읽기 사이에 파일이 바뀌는 경우 |
| staging | manifest/proof는 객체 스토리지 조건부 쓰기(S3 `If-None-Match`, GCS generation=0, Azure `If-None-Match`)로 한 번만 생성된다 | 조건부 쓰기를 지원하지 않는 S3 호환 스토리지 — probe가 "새 키가 이미 존재" 응답을 감지하면 거부하지만 모든 오동작을 잡지는 못한다 |
| 환경 검증 | `ballista plan --probe`가 spark-submit, API `/readyz`, 드라이버 SA RBAC, staging 쓰기·읽기, Iceberg 카탈로그, digest 고정 이미지를 실제로 확인한다 | 이미지 내부 내용 검증 — digest 고정만 확인한다. `allow_mutable_image: true`는 로컬·E2E 전용 |

`arsenal.yaml`의 `distributed:` 단계에서 `input_version: auto`는 실행할 때마다 입력 prefix의
목록 fingerprint로 바뀌고, run id도 그 값으로 정해진다 — 입력이 그대로면 같은 run을 reconcile하고,
바뀌었으면 새 run을 제출한다. `auto`는 Parquet 입력만 지원하며, 목록을 읽을 수 없으면 `arsenal run`은
제출 전에 실패하고 `arsenal status`는 `input-unresolved`를 보고한다.

### DuckDB ↔ Spark 동치성 (LogicalPlan v1)

같은 portable plan이 두 엔진에서 같은 결과를 내도록 Spark 렌더러를 DuckDB 의미에 맞췄고,
골든 corpus(`packages/ballista/tests/portable_corpus.py`)로 고정한다.

- rename된 컬럼은 두 엔진 모두 **맨 뒤로** 이동한다 (DuckDB `* EXCLUDE` 의미).
- `cast` 단계의 실수→정수는 반올림한다: DOUBLE은 half-to-even, DECIMAL은 half-away-from-zero.
- `decimal`은 `DECIMAL(38, 9)`이다.
- `concat()`은 portable이 아니다 — NULL 처리가 엔진마다 다르다. `||`를 쓴다.
- **맞추지 못한 것:** `derive`/`filter` 표현식 **안의** `CAST(... AS INTEGER)`는 문자열 그대로
  각 엔진에 전달되므로 Spark에서는 절사된다. 실수→정수 변환은 `cast` 단계로 하라.
- `dedup`은 두 엔진 모두 키당 **임의의** 행을 남긴다. 키 외 컬럼이 다른 중복은 결과가 엔진마다 다를 수 있다.

## Aquila 카탈로그의 보장과 한계 (0.5 preview)

| 항목 | 보장하는 것 | 보장하지 않는 것 |
|---|---|---|
| 멱등 | 같은 입력 재스캔 시 데이터셋·스키마 버전·간선 행 수가 늘지 않는다. 스펙이 입력을 바꾸면 그 스펙의 옛 간선은 지운다 | 지워진 스펙 파일의 간선 — 스캔 대상에서 사라진 스펙의 간선은 남는다 |
| 정체성 | 같은 위치는 하나의 데이터셋(스킴 소문자, 끝 `/`·glob 제거, 쿼리·자격증명 제거) | 심볼릭 링크·마운트가 다른 두 경로, 대소문자를 무시하는 파일시스템의 같은 경로 |
| lineage | 스펙 선언 기반의 데이터셋 단위 간선, 순환 감지 | 실제 읽기 관측(스펙에 없는 경로를 읽는 SQL), 컬럼 단위 lineage |
| 스키마 이력 | Pugio가 기록한 모든 스냅샷 + 로컬 Parquet footer(이름순 마지막 파일). 버전 키는 schema hash | 원격(S3/GCS)·Iceberg·DuckDB·Postgres 데이터셋의 스키마. A→B→A로 돌아온 스키마는 한 버전으로 합쳐진다 |
| 계약·거버넌스 | 계약 해시 변경 감지, 최신 스키마와의 불일치 보고, 태그·deprecate·FTS 검색 | 강제 — 접근 제어는 서버 없이는 불가, 계약 강제는 Scutum의 몫 |
| 다른 도구에 대한 영향 | 상태 DB는 읽기 전용으로 열어 마이그레이션·백업·잠금을 일으키지 않는다 | 스캔과 동시에 쓰이는 WAL DB의 마지막 커밋 반영(다음 스캔에서 반영) |

## 변환 스펙의 신뢰 경계

**`transform.yaml`을 작성할 수 있는 사람은 그 프로세스에서 SQL을 실행할 수 있는
사람과 같다.** dbt 모델과 동일한 신뢰 모델이다 — 스펙 작성자와 자격증명 소유자가
같은 사람이라는 전제 위에 서 있다.

방어하는 것과 하지 않는 것을 나눠 적는다:

| 위치 | 처리 | 이유 |
|---|---|---|
| 식별자(컬럼명·별칭·dedup 키) | 인용 + `"` 이스케이프 | 구조를 깨는 위치 |
| cast 타입 | 화이트리스트 8종, 그 외 `FatalError` | 구조를 깨는 위치 |
| 경로(input/output) | `'` 이스케이프한 SQL 리터럴 | 구조를 깨는 위치 |
| **`;` (문 구분자)** | **filter·derive·map·sql 전부 거부** | 래퍼를 닫고 **두 번째 문**을 여는 것을 막는다 |
| **표현식 삽입 지점** | **괄호로 감싼다** (`WHERE (…)`, `(…) AS "c"`) | 여분의 `)`가 스코프 탈출이 아니라 파싱 에러가 되게 |
| 표현식 *내용* | **검증하지 않는다** | 사용자 자신의 SQL — 파싱해서 막으려는 건 지는 군비 경쟁이고 탈출구를 망가뜨린다 |

마지막 줄의 결과로 **남아 있는 것**을 숨기지 않는다: 표현식 안의 서브쿼리는 유효한
SQL이므로, `derive`로 `(SELECT … FROM read_csv('/etc/passwd'))` 같은 로컬 파일 읽기가
가능하다. 이건 버그가 아니라 위 신뢰 모델의 직접적 귀결이다. 따라서:

- **스펙이 신뢰 경계를 넘는 순간 전제가 깨진다** — 공유 레시피 저장소, SaaS 래퍼,
  PR이 `transform.yaml`을 고칠 수 있는 CI. 그런 환경에서는 스펙을 코드와 동일하게
  취급해 리뷰해야 한다.
- `arsenal init <recipe>`로 배포되는 레시피는 이 저장소가 소유한 것만 사용한다.
  서드파티 레시피를 받아 실행하는 기능은 범위 밖이다.

## 관련 문서

- [01-scope.md](01-scope.md) — P0/P1/P2 범위 확정과 마일스톤별 DoD
- [07-cost-efficiency.md](07-cost-efficiency.md) — "줄여주지 못하는 비용" (정직한 한계선의 비용 관점)
- [roadmap.md](roadmap.md) — 도구별 문제 정의와 P0/P1/P2 원본 로드맵
- [reference/packaging.md](reference/packaging.md) — 패키징·PyPI 이름 확인 노트
