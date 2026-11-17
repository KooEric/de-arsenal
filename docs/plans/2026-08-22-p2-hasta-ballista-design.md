# P2 Hasta · Ballista 제품 설계 및 구현 계획

> 상태: **승인 대기 설계안**  
> 작성일: 2026-08-22  
> 범위: 스트리밍·CDC(**Hasta**)와 대규모 분산 실행(**Ballista**)을, 기존
> DE Arsenal의 "선언은 그대로, 실행만 확장" 원칙에 맞게 제품화한다.

## 0. 결론과 착수 순서

P2는 `Kafka를 붙이고 Spark를 호출하는 기능`이 아니다. 기존의 재실행·품질·상태
계약을 **마이크로배치와 원격 실행에도 보존**하는 제품 확장이다. 따라서 다음
순서를 고정한다.

```text
P2-0 실행·상태·커밋 기반
  ├── P2-1 Hasta v1: 폴링 마이크로배치
  ├── P2-2 Hasta v1.1: CDC(outbox, then table cursor)
  └── P2-3 Ballista v1: 분산 변환 + Spark on Kubernetes 어댑터
        └── P2-4 운영 완성: 성능·복구·배포·문서·릴리스
```

Hasta와 Ballista를 동시에 구현하지 않는다. P2-0의 영속 실행 계약과 커밋 규약을
먼저 만든 뒤, 상태 전이와 재시작을 작은 로컬 마이크로배치에서 증명한다. 그 증명을
그대로 Ballista의 원격 작업·재조정(reconcile)에 사용한다.

첫 Ballista의 **운영용** 백엔드는 `Spark on Kubernetes`의 `spark-submit` cluster
mode로 한정한다. 이는 작업 식별자와 상태·취소 조회가 필요한 제품 경로다. Spark
Connect는 개발·대화형 검증용 어댑터 후보일 뿐, P2 v1의 장애 복구 보장을 맡기지
않는다. Spark Connect는 논리 계획 기반의 원격 DataFrame API이지만 세션형
클라이언트이고 모든 실행 API를 제공하지 않는다. 반면 Kubernetes의 Spark 제출은
제출 ID로 상태 조회·취소가 가능하다. [Spark Connect 개요](https://spark.apache.org/docs/latest/spark-connect-overview.html),
[Spark on Kubernetes 작업 관리](https://spark.apache.org/docs/latest/running-on-kubernetes.html)

이는 별도의 “PySpark 학습 앱”을 만드는 일이 아니다. Ballista 내부의 선택형 Spark
어댑터가 Spark의 공식 Python 런타임을 사용할 수는 있지만, 사용자는 계속 Arsenal
YAML과 CLI만 쓴다. 로컬 DuckDB 경로는 기본값으로 유지하며, 분산 인프라는 필요한
사용자만 선택한다.

## 1. 현황 감사

### 1.1 감사 범위와 결과

2026-08-22 기준으로 코드·스키마·테스트·CI·문서를 읽고 현재 워크스페이스에서
검증했다.

| 항목 | 확인 결과 | P2에 주는 의미 |
|---|---|---|
| 배포 단위 | `arsenal-core`, `pugio`, `gladius`, `arsenal`, `scutum`, `scorpio`, `spatha`, `onager` 8개 패키지 | Hasta/Ballista도 독립 배포 패키지로 추가 가능 |
| 데이터 경로 | Pugio가 Arrow batch를 받아 Parquet/DuckDB/Postgres로 멱등 write, Gladius가 Parquet을 DuckDB SQL로 변환 | Arrow/Parquet 허브는 유지하되 원격 결과의 commit 규약이 추가로 필요 |
| 신뢰성 | 결정적 unit ID, SQLite WAL, `write → done` 순서, REST cursor와 done을 하나의 SQLite commit으로 처리 | P2도 at-least-once 실행 + 멱등 결과를 기본 불변식으로 삼을 수 있음 |
| 품질·운영 | Scutum contract/DLQ, Scorpio freshness·lineage, Spatha DAG, Onager backfill/compaction 존재 | P2가 새 검증·관측 체계를 만들지 말고 이 패키지들을 확장해야 함 |
| 현재 검증 | `pytest`: **338 passed, 21 skipped**, coverage **89.91%** | P2 시작 전 기능 기준선으로 사용 |
| 현재 정적 게이트 | `ruff check` 및 `ruff format --check` 통과. `pyright`는 워킹트리의 미추적 `packages/onager/tests/test_cli.py:10`에서 `pyarrow.parquet.write_table` 타입이 부분 unknown이라 실패 | 이 파일은 P2 범위 밖의 기존 미커밋 변경이다. P2 브랜치/PR에 들어가기 전에 별도로 해결해 전체 CI green을 회복해야 함 |

감사 중인 워킹트리에는 README, dogfooding, Onager CLI 및 테스트, 스크립트의
사용자 변경도 존재한다. 이 설계의 구현은 그 변경을 덮어쓰지 않고 별도 커밋으로
진행한다.

### 1.2 이미 충족한 기반

다음은 새로 만들지 않고 반드시 재사용한다.

- `StateStore`의 결정적 `UnitSpec`, 단위 행/바이트/시간 지표, cursor, lineage.
- Pugio의 retry·인증 갱신·data contract·DLQ·sink idempotency 계약.
- Gladius의 선언형 transform spec, `map → steps → SQL → Python` 하강 경로,
  증분 입력 추적.
- Scutum의 파일 잠금, Spatha의 deterministic DAG, Scorpio의 freshness/cost
  집계, Onager의 격리 backfill/compaction 안전장치.
- 스키마 문서 자동생성, strict Pyright, 3 OS CI, 신뢰성 E2E라는 저장소 규칙.

### 1.3 P2를 막는 정확한 공백

| 공백 | 현재 상태 | 해결 원칙 |
|---|---|---|
| 실행 추상화 | `gladius.engine`이 DuckDB 연결·SQL·파일 교체를 직접 소유 | 논리 계획과 엔진 실행기를 분리한다 |
| 스트림 상태 | `units`와 REST cursor만 있고 stream batch/run/lease/watermark가 없음 | SQLite에 버전 관리되는 stream 상태 모델을 추가한다 |
| 다중 프로세스 안전성 | 파일 lock은 있으나 실행 소유권·fencing·lease가 없음 | lease token을 가진 coordinator 하나만 commit하게 한다 |
| 원격 결과 commit | 로컬 Parquet은 `os.replace`, 원격 객체 스토리지는 결정적 키 write만 지원 | commit capability를 명시하고, multi-writer/upsert는 Iceberg 또는 DB transaction으로 제한한다 |
| portable transform | Gladius SQL은 DuckDB 문법·raw SQL·Python UDF를 허용 | 엔진 공통 논리 계획과 이식성 등급을 도입하고, 지원하지 않는 step은 preflight에서 거부한다 |
| 원격 작업 lifecycle | 작업 ID, 제출/조회/취소/reconcile가 없음 | Ballista `RemoteExecutor` 계약과 job ledger를 추가한다 |
| 운영 검증 | Docker 기반 DB 테스트는 있으나 Kafka/Spark/객체 스토리지 E2E가 없음 | unit → local integration → opt-in container/Kubernetes 순으로 계층화한다 |

## 2. 제품 경계와 불변식

### 2.1 P2가 제공하는 것

1. **Hasta v1**은 foreground 프로세스의 폴링 마이크로배치다. REST 또는 변경
   이벤트를 주기적으로 읽고, 지속 checkpoint·품질 검증·DLQ·멱등 sink write를
   수행한다. `--once`와 `--max-batches`로 배치처럼 테스트·운영할 수 있다.
2. **CDC v1**은 append-only outbox를 1급 소스로 지원한다. insert/update/delete
   이벤트를 식별 가능한 `event_id`·단조 `sequence`·`op`로 전달하고, DB sink에는
   atomic merge/delete를 적용한다.
3. **Ballista v1**은 같은 transform YAML의 portable subset을 Spark Kubernetes
   작업으로 실행한다. 분산 실행은 선택 사항이며, input/output은 모든 worker가
   접근 가능한 URI와 명시적 commit capability를 요구한다.
4. 사용자가 확인할 수 있는 상태는 `arsenal status`, `hasta status`,
   `ballista status`, `ballista reconcile`, `ballista cancel`로 일관되게 보여 준다.

### 2.2 P2 v1에서 명시적으로 하지 않는 것

- Kafka, Debezium, binlog/WAL 직접 읽기, Flink, 밀리초급 연속 처리.
- 자체 분산 scheduler, 장기 상주 control-plane 서비스, 클라우드 관리형 SaaS.
- raw S3/GCS Parquet에 대한 multi-writer atomic replace/upsert 보장.
- Spark RDD API, 임의 raw SQL, 임의 Python UDF의 DuckDB/Spark 자동 동치화.
- source 시스템의 삭제를 행 스냅샷만 보고 추론하는 CDC.

Kafka와 log-based CDC는 V1의 source protocol을 구현하는 후속 어댑터다. 핵심
checkpoint/commit 계약을 먼저 검증하지 않은 채 Kafka를 첫 소스로 넣지 않는다.

### 2.3 전 제품 불변식

| ID | 불변식 |
|---|---|
| I1 | 같은 입력 위치와 같은 pipeline revision은 같은 batch/run identity를 가진다. |
| I2 | checkpoint는 sink 결과가 durable하게 commit된 뒤에만 전진한다. |
| I3 | checkpoint 전진과 batch `committed` 상태는 하나의 StateStore transaction으로 기록한다. |
| I4 | crash 후 재시도는 같은 logical batch/run을 재사용하며, 보이는 결과에는 중복이나 partial output을 남기지 않는다. |
| I5 | lease token이 없는 runner는 상태·checkpoint·출력 publish를 commit할 수 없다. |
| I6 | 품질 실패 데이터는 정상 sink로 나가지 않으며 정책대로 block/warn/DLQ가 된다. |
| I7 | engine이 바뀌어도 portable transform의 입력 스키마·정렬 비의존 결과·출력 스키마는 동일하다. |
| I8 | 보장 수준이 sink/engine/storage capability보다 강하게 표시되지 않는다. |

`exactly-once`라는 표현은 I1~I5 및 지원 sink가 모두 충족될 때의 **결과
exactly-once**에만 사용한다. 전송 자체는 at-least-once이며, 지원하지 않는 sink는
CLI가 보장 수준을 낮춰 출력하거나 실행을 거부한다.

## 3. 목표 아키텍처

### 3.1 의존 방향과 패키지 구조

```text
                         ┌──────────────────────┐
                         │    arsenal-core       │
                         │ execution · state     │
                         │ artifacts · leases    │
                         └───────┬───────┬──────┘
                                 │       │
                  ┌──────────────┘       └──────────────┐
                  ▼                                     ▼
             pugio · scutum                         gladius
                  │                                     │
                  ▼                                     ▼
                hasta ────────(Parquet/DB/Iceberg)──► ballista
                  │                                     │
                  └───────────────┬─────────────────────┘
                                  ▼
                         arsenal umbrella CLI
```

새 의존 방향은 `ballista → gladius`와 `hasta → pugio`만 허용한다. 반대 방향 import는
금지한다. `arsenal-core`는 Spark, Kubernetes, PyArrow에 의존하지 않는다.

```text
packages/
├── arsenal-core/src/arsenal_core/
│   ├── execution/{models.py,protocol.py,capabilities.py,artifacts.py}
│   └── state/{store.py,migrations.py,stream.py,leases.py}
├── pugio/                         # sink capability + CDC DB apply adapter 추가
├── gladius/                       # LogicalPlan + local DuckDB executor로 분리
├── hasta/src/hasta/
│   ├── spec.py                    # StreamSpec/CDCSpec validation
│   ├── model.py                   # StreamPosition, MicroBatch, ChangeEvent
│   ├── sources/{base.py,rest_poll.py,outbox.py,table_cursor.py}
│   ├── runner.py                  # lease/poll/validate/write/checkpoint loop
│   ├── commit.py                  # Pugio sink capability bridge
│   ├── cli.py
│   └── py.typed
├── ballista/src/ballista/
│   ├── config.py                  # backend profile and credentials-by-env validation
│   ├── plan.py                    # Gladius LogicalPlan portability gate
│   ├── runner.py                  # run ledger, submit/reconcile/cancel
│   ├── executors/{base.py,spark_kubernetes.py}
│   ├── worker.py                  # Spark driver entry point
│   ├── artifacts.py               # immutable staging + publish proof
│   ├── cli.py
│   └── py.typed
└── arsenal/                       # hasta/ballista command delegation and project dispatch
```

배포명은 `de-hasta`, `de-ballista`로 한다. 기본 `de-arsenal` 설치는 계속 가볍게
유지하고 아래처럼 opt-in extra만 추가한다.

```toml
# de-hasta
dependencies = ["arsenal-core", "pugio", "de-scutum", "de-scorpio", "pyarrow", "typer"]
postgres = ["pugio[postgres]"]

# de-ballista
dependencies = ["arsenal-core", "de-gladius", "typer"]
spark-kubernetes = ["kubernetes>=…"]
iceberg = ["pyiceberg>=…"]

# de-arsenal
streaming = ["de-hasta"]
distributed = ["de-ballista"]
```

Spark worker의 Python runtime, Spark image, Kubernetes cluster와 Iceberg catalog는
사용자가 제공하는 인프라다. 패키지를 기본 설치에 넣거나 로컬 클러스터를 몰래
기동하지 않는다.

### 3.2 공통 실행 계약

P2-0에서 다음의 engine-중립 모델을 `arsenal_core.execution`에 도입한다. 이 모델은
Pydantic/YAML 모델이 아니라 frozen dataclass + JSON 직렬화 가능한 primitive만
사용한다.

```python
@dataclass(frozen=True)
class ArtifactRef:
    uri: str
    format: Literal["parquet", "iceberg", "duckdb", "postgres"]
    version: str | None          # input snapshot/manifest/version; 없으면 unsafe
    content_hash: str | None

@dataclass(frozen=True)
class RunContext:
    run_id: str                  # UUID; 재조정 시 같은 값 재사용
    pipeline: str
    revision: str                # canonical spec hash
    lease: LeaseToken
    started_at: datetime

@dataclass(frozen=True)
class ExecutionRequest:
    context: RunContext
    plan: Mapping[str, object]   # versioned logical plan JSON
    inputs: tuple[ArtifactRef, ...]
    output: ArtifactRef
    guarantees: GuaranteeRequest

class Executor(Protocol):
    name: str
    capabilities: ExecutorCapabilities
    def preflight(self, request: ExecutionRequest) -> None: ...
    def submit(self, request: ExecutionRequest) -> JobHandle: ...
    def status(self, handle: JobHandle) -> JobStatus: ...
    def cancel(self, handle: JobHandle) -> None: ...
```

`GuaranteeRequest`는 `result_exactly_once`, `atomic_publish`, `delete_apply`,
`watermark`를 요구 조건으로 나타낸다. `ExecutorCapabilities`와 sink/storage
capability의 교집합이 요청보다 약하면 preflight가 사람에게 이해되는 오류로
중단한다. “될 수도 있다”는 실행을 하지 않는다.

### 3.3 artifact 및 commit capability

모든 writer는 다음 capability를 등록한다.

| capability | 가능한 구현 | 허용 사용 |
|---|---|---|
| `idempotent_batch_write` | 결정적 unit/batch 파일명, DB MERGE | append microbatch 재시도 |
| `atomic_publish` | 로컬 `os.replace`, DB transaction, Iceberg snapshot commit | 실패 후 partial output 은닉 |
| `delete_apply` | Postgres/DuckDB transaction, Iceberg row-level delete가 가능한 catalog | CDC delete 적용 |
| `concurrent_writers` | transaction 또는 atomic catalog commit | Ballista/복수 runner |
| `input_versioning` | Iceberg snapshot, immutable manifest, DB snapshot token | 분산 재실행의 동일 input 판정 |

raw object-store Parquet은 v1에서 `idempotent_batch_write`까지만 광고한다. 원격
multi-writer replace/upsert가 필요하면 Iceberg catalog 또는 transactional DB를
필수로 한다. Iceberg sink는 Ballista 구현 전에 P2-0의 별도 work item으로 추가한다.
이 결정을 생략하면 객체 스토리지 rename을 원자적이라고 오인해 데이터 손실을 만들게
된다.

### 3.4 공유 StateStore 확장과 migration

기존 `CREATE TABLE IF NOT EXISTS`만으로는 state schema의 진화를 안전하게 관리할 수
없다. P2-0에서 `PRAGMA user_version` 기반의 순방향 migration runner를 도입한다.

1. `StateStore.open()`은 현재 DB version을 확인한다.
2. migration 전, `.arsenal/backups/<db>-v<N>-<timestamp>.sqlite`에 SQLite backup API로
   복구용 사본을 만든다.
3. 각 migration은 하나의 `BEGIN IMMEDIATE` transaction에서 실행하고 version을
   올린다.
4. 더 새 DB를 오래된 패키지로 여는 downgrade는 거부한다. downgrade 명령은 제공하지
   않는다.
5. `arsenal state migrate <state.db> --dry-run`과 `--backup-dir`를 제공한다.

추가 테이블의 개략은 다음과 같다. 실제 migration에는 index와 foreign-key 검증도
포함한다.

```sql
CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  pipeline TEXT NOT NULL,
  revision TEXT NOT NULL,
  mode TEXT NOT NULL,                    -- stream | transform
  executor TEXT NOT NULL,
  status TEXT NOT NULL,                  -- planned|running|succeeded|failed|cancelled|lost
  guarantee_json TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 0,
  external_job_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE leases (
  resource TEXT PRIMARY KEY,             -- stream:<pipeline> / run:<run_id>
  owner_id TEXT NOT NULL,
  fence INTEGER NOT NULL,
  expires_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE stream_checkpoints (
  pipeline TEXT NOT NULL,
  stream_name TEXT NOT NULL,
  generation INTEGER NOT NULL,
  position_json TEXT NOT NULL,
  watermark TEXT,
  last_batch_id TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (pipeline, stream_name)
);

CREATE TABLE stream_batches (
  batch_id TEXT PRIMARY KEY,
  pipeline TEXT NOT NULL,
  stream_name TEXT NOT NULL,
  revision TEXT NOT NULL,
  start_position_json TEXT NOT NULL,
  end_position_json TEXT NOT NULL,
  event_hash TEXT NOT NULL,
  status TEXT NOT NULL,                  -- planned|claimed|running|committed|failed|quarantined
  output_ref_json TEXT,
  rows INTEGER,
  bytes INTEGER,
  late_rows INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX stream_batches_identity
  ON stream_batches(pipeline, stream_name, revision, start_position_json, end_position_json);
```

SQLite DB는 단일 coordinator host에서만 지원한다. NFS/공유 파일시스템에 같은 state
DB를 두고 여러 host가 writer가 되는 구성은 명시적으로 거부한다. 분산 cluster의
worker는 state DB를 직접 열지 않고 manifest와 artifact만 읽는다.

### 3.5 lease와 fencing

`leases`는 source poll과 publish를 직렬화한다.

- 시작 시 `acquire(resource, owner_id, ttl)`는 SQLite의 immediate transaction 안에서
  만료 lease를 원자적으로 인수하고 증가한 `fence`를 반환한다.
- 실행 중 TTL의 1/3마다 `renew`한다. renewal 실패는 즉시 안전 정지한다.
- `commit_*` SQL은 `owner_id`, `fence`, `expires_at > now`를 조건으로 검사한다.
- SIGTERM은 새 poll을 멈추고 현재 batch의 sink write를 끝낸 다음 checkpoint commit
  또는 failure 기록을 시도한다. 제한 시간 후 SIGKILL은 I4의 재시도로 복구한다.

## 4. Hasta v1 설계

### 4.1 사용자 경험과 YAML

기존 `PipelineSpec`은 그대로 유효하다. P2가 추가하는 것은 optional `stream` 블록과
새 stream source type뿐이다. `stream`이 없는 기존 YAML은 의미·기본값·출력이
바뀌지 않는다. `pugio run`은 stream YAML을 발견하면 `hasta run`을 안내하고, `hasta
run`은 stream 블록 없는 YAML을 거부한다.

```yaml
# orders-stream.yaml
name: orders-stream
state_dir: .arsenal

source:
  type: rest_poll
  url: https://api.example.com/orders/changes
  headers: {Authorization: "Bearer ${ORDERS_API_TOKEN}"}
  cursor:
    request_param: after
    response_path: next_cursor
    event_id_path: id
  record_path: data

stream:
  name: orders
  poll_interval_seconds: 15
  batch: {max_records: 10000, max_bytes: 67108864, max_wait_seconds: 20}
  event_time:
    field: occurred_at
    allowed_lateness: PT10M
    on_late: quarantine                 # drop | quarantine | append
  checkpoint: {lease_ttl_seconds: 60}

contract:
  columns:
    - {name: id, type: string, nullable: false}
    - {name: occurred_at, type: timestamp[us, tz=UTC], nullable: false}
  on_violation: quarantine

sink:
  type: parquet
  path: ./data/orders-raw
```

CDC outbox는 별도 source type으로 명확하게 표현한다. snapshot table과 변경 로그를
혼동하지 않는다.

```yaml
source:
  type: database_outbox
  dialect: postgres
  dsn_env: APP_DATABASE_URL
  table: public.order_outbox
  sequence_column: sequence
  event_id_column: event_id
  operation_column: op                  # I | U | D
  key_columns: [order_id]
  payload_column: payload
stream:
  name: orders-cdc
  poll_interval_seconds: 5
  batch: {max_records: 5000, max_bytes: 33554432, max_wait_seconds: 10}
  checkpoint: {lease_ttl_seconds: 30}
sink:
  type: postgres
  dsn_env: ANALYTICS_DATABASE_URL
  table: orders
  merge_key: [order_id]
```

CLI는 다음으로 고정한다.

```text
hasta run orders-stream.yaml              # foreground loop
hasta run orders-stream.yaml --once        # poll 가능한 batch 최대 하나만 처리
hasta run orders-stream.yaml --max-batches 100
hasta status orders-stream.yaml
hasta checkpoint show orders-stream.yaml
hasta checkpoint reset orders-stream.yaml --to <position> --confirm
```

reset은 destructive operation이다. 실행 중 lease가 있으면 거부하고, 현재 checkpoint와
target을 출력한 뒤 `--confirm` 없이는 실행하지 않는다. reset은 새 generation을 만들며
기존 batch ledger는 보존한다.

### 4.2 stream protocol

```python
@dataclass(frozen=True)
class StreamPosition:
    value: Mapping[str, object]          # cursor/sequence/timestamp+tie-breaker

@dataclass(frozen=True)
class MicroBatch:
    start: StreamPosition
    end: StreamPosition
    events: pa.Table
    event_hash: str                      # canonical event_id sequence hash
    source_exhausted: bool

class StreamingSource(Protocol):
    source_id: str
    def poll(self, after: StreamPosition | None, limits: BatchLimits) -> MicroBatch: ...
    def close(self) -> None: ...

class StreamingSink(Protocol):
    capabilities: SinkCapabilities
    def write_batch(self, batch: CommittedBatch, table: pa.Table) -> OutputProof: ...
    def apply_changes(self, batch: CommittedBatch, changes: pa.Table) -> OutputProof: ...
```

`batch_id`는 다음 원소의 canonical JSON을 SHA-256으로 hash하여 만든다.

```text
pipeline + stream_name + spec_revision + source_id + start_position + end_position + event_hash
```

source가 stable event ID를 제공하지 않으면 `rest_poll`은 exactly-once 보장을 광고하지
않는다. 응답 전체 hash만으로는 API가 레코드를 재정렬하거나 수정할 때 중복/누락을
판단할 수 없기 때문이다.

### 4.3 처리와 commit 알고리즘

```text
acquire stream lease
  │
  ├─ load checkpoint C0
  ├─ source.poll(C0) → candidate [P0, P1, events]
  ├─ batch_id 계산, stream_batches에 planned 등록 (멱등)
  ├─ contract/schema/watermark/DLQ 검사
  ├─ sink.write_batch(batch_id, events) 또는 sink.apply_changes(...)
  │      └─ deterministic batch identity를 가진 멱등 write
  └─ 한 SQLite transaction:
       batch.status = committed + output proof 기록
       checkpoint = P1, generation 유지, watermark 전진
```

sink write와 SQLite commit 사이의 crash가 핵심이다. 재기동 후 checkpoint가 C0이므로
같은 batch ID를 다시 계산한다. sink는 같은 ID의 결과를 다시 써도 결과가 변하지
않아야 하며, 그 뒤 checkpoint가 한 번만 P1으로 전진한다. 이를 만족하지 않는 sink는
streaming mode에 등록할 수 없다.

watermark는 event-time 최대값에서 `allowed_lateness`를 뺀 **보수적 기준**이다. 이는
source position을 대체하지 않는다. late event는 `drop`, `quarantine`, `append` 중
명시된 정책을 따르고, `late_rows`와 샘플 reason을 Scorpio에 기록한다. v1 raw ingestion
은 stateful window aggregation을 제공하지 않는다. 그 기능은 logical plan + engine
checkpoint가 도입된 후의 별도 단계다.

### 4.4 CDC 모델

CDC event envelope는 source마다 달라져서는 안 된다.

```text
event_id:      immutable source event identifier
sequence:      source-local strictly increasing position
op:            I | U | D
key:           merge/delete key object
occurred_at:   UTC event time
payload:       I/U의 full after-image; D는 key만 허용
schema_version: optional producer version
```

v1의 권장 소스는 transaction과 함께 쓰는 **outbox table**이다. outbox producer는
business row 변경과 event insert를 같은 DB transaction으로 실행해야 한다. Hasta는
`sequence > checkpoint.sequence ORDER BY sequence LIMIT n`으로 poll한다.

`database_table_cursor`는 후속 v1.1에서 제공한다. `(updated_at, primary_key)`의
lexicographic cursor로 ordered snapshot delta를 읽으며 아래 한계를 CLI와 문서에
강제 표기한다.

- hard delete를 관찰할 수 없다. delete가 필요하면 outbox를 사용한다.
- cursor column은 immutable 또는 단조 증가해야 하며 timezone이 있는 UTC timestamp여야
  한다.
- 같은 timestamp에서 tie-breaker primary key가 필수다.
- update가 cursor보다 과거 timestamp를 쓰면 누락되므로 schema validation에서 금지한다.

CDC를 apply할 수 있는 sink는 Postgres/DuckDB의 transaction path와 atomic Iceberg
table뿐이다. Parquet sink는 `change_log` append만 지원하며 current-state table로
표시하지 않는다.

## 5. Ballista v1 설계

### 5.1 논리 계획을 먼저 만든다

현재 Gladius는 transform spec을 DuckDB SQL로 바로 compile한다. Ballista가 같은 YAML을
실행하려면 SQL 문자열이 아니라 engine-neutral `LogicalPlan`이 필요하다.

```text
TransformSpec
   │
   ├── validate portable subset ──→ LogicalPlan v1 ──┬── DuckDB renderer/executor
   │                                                  └── Spark renderer/worker
   └── local-only steps ──→ existing DuckDB SQL path
```

`LogicalPlan v1`의 node는 `ReadParquet`, `Project`, `Rename`, `Cast`, `Filter`,
`Derive`, `Deduplicate`, `Write`다. `Filter`와 `Derive` expression은 SQLGlot으로
parse해 ANSI subset AST로 보관하고 DuckDB/Spark renderer가 각각 dialect SQL로
render한다. parser가 지원하지 않거나 dialect별 의미가 다른 expression은 portable
mode에서 parse 단계에 거부한다.

이식성 등급은 다음과 같다.

| transform 구성 | 등급 | local DuckDB | Ballista v1 |
|---|---|---|---|
| map, rename, select, cast, dedup, ANSI filter/derive | `portable` | 지원 | 지원 |
| DuckDB 전용 function/type | `duckdb_only` | 지원 | preflight 거부 |
| `sql:` escape hatch | `engine_local` | 지원 | preflight 거부 |
| `python:` UDF | `engine_local` | 지원 | preflight 거부 |

기존 `TransformSpec`의 의미를 바꾸지 않는다. `gladius run`은 계속 local SQL path를
기본으로 사용한다. `ballista run` 또는 `gladius run --executor ballista`만 portable
검증을 요구한다. 변환 문서에는 각 step의 portability를 자동 표기한다.

### 5.2 Ballista user interface

```yaml
# transform YAML은 변경 없이 유지한다.
name: daily-revenue
input: s3://lake/silver/orders/
steps:
  - filter: "status = 'paid'"
  - derive: {gross_revenue: "quantity * unit_price"}
  - select: [order_id, customer_id, gross_revenue]
output: iceberg://analytics.gold.daily_revenue
```

환경 고유 설정은 transform YAML과 분리된 executor profile로 둔다. 따라서 개발자는
같은 transform을 local과 cluster에서 비교할 수 있다.

```yaml
# ballista-k8s.yaml — secret은 환경변수로만 참조
backend: spark_kubernetes
cluster:
  context_env: KUBECONFIG
  namespace: data-jobs
  driver_image: registry.example.com/de-arsenal-spark:0.3.0
  service_account: de-arsenal
storage:
  staging_uri: s3://lake/_arsenal/staging
  iceberg_catalog:
    type: rest
    uri_env: ICEBERG_REST_URI
    token_env: ICEBERG_REST_TOKEN
spark:
  executor_instances: 4
  executor_cores: 2
  executor_memory: 4g
```

```text
ballista plan transform.yaml --profile ballista-k8s.yaml
ballista run transform.yaml --profile ballista-k8s.yaml
ballista status <run-id> --profile ballista-k8s.yaml
ballista reconcile --state-dir .arsenal --profile ballista-k8s.yaml
ballista cancel <run-id> --profile ballista-k8s.yaml
```

`ballista plan`은 rendered logical plan, requested/available capabilities, input
version, output commit strategy, 예상 Spark resource 설정을 출력한다. preflight를
통과하지 못한 이유를 Spark traceback 대신 이 단계에서 알려 준다.

### 5.3 Spark Kubernetes executor

Ballista는 remote Spark application을 다음처럼 관리한다.

1. coordinator가 input immutable version, logical-plan JSON, output target, run ID,
   fencing token을 포함한 immutable run manifest를 staging URI에 쓴다.
2. `runs`에 `planned` row를 먼저 commit한다. Kubernetes driver pod 이름은
   `arsenal-<run-id-short>`으로 결정적이다.
3. `spark-submit --deploy-mode cluster`로 `ballista.worker`를 제출하고, run ID를
   Kubernetes labels와 Spark application name에 넣는다.
4. worker는 manifest의 exact input version만 읽고 portable Spark plan을 실행한다.
   output은 Iceberg transaction 또는 storage capability가 보장하는 staged commit으로
   publish한 뒤 `OutputProof`를 staging에 쓴다.
5. coordinator는 Kubernetes submission ID/driver pod reference를 `runs`에 기록하고
   상태를 poll한다. 완료 proof가 확인된 뒤에만 `runs.succeeded`를 commit한다.
6. coordinator가 죽으면 `ballista reconcile`이 run ID/결정적 pod name으로 cluster를
   조회한다. active면 관찰을 재개하고, completed+proof면 commit하며, absent+proof 없음
   이면 attempt를 증가시켜 같은 logical run을 재제출한다.

Kubernetes backend는 submit ID를 사용한 Spark status/kill 경로를 제공하므로 P2 v1의
운영 backend로 택한다. [Apache Spark Kubernetes 문서](https://spark.apache.org/docs/latest/running-on-kubernetes.html)
는 cluster mode의 submission ID 기반 status/kill을 명시한다. Spark Connect는 향후
`InteractiveExecutor`로 추가하되, 같은 durable reconciliation contract를 만족하는
방식이 검증될 때까지 production guarantee를 표시하지 않는다.

**원격 preflight는 필수다.** 다음 중 하나라도 없으면 submit하지 않는다.

- 모든 executor가 읽을 수 있는 versioned input URI (`file://`와 로컬 상대경로 금지).
- `atomic_publish`와 `concurrent_writers`를 제공하는 output (v1은 Iceberg 또는
  transactional DB output만 production mode 허용).
- worker image의 Arsenal/Apache Spark compatibility matrix 일치.
- Kubernetes RBAC: create/get/list/delete driver·executor resource 권한.
- staging URI에 대한 read/write, catalog credential, namespace/service account 존재.
- portable `LogicalPlan v1` 및 지원 Spark SQL function/type 집합.

### 5.4 Spark worker 경계

worker는 최대한 작고 결정적이어야 한다. Kubernetes client, state DB, CLI parsing을
driver/executor로 옮기지 않는다.

```text
manifest URI → validate signature/schema → read versioned input
             → LogicalPlan Spark renderer → write transaction
             → output-proof JSON → exit code
```

- worker에는 secret 값을 manifest로 복사하지 않는다. service account/secret mount 또는
  runtime 환경변수만 사용한다.
- Spark configuration은 allowlist(`spark.executor.*`, `spark.sql.*` 등)만 profile에서
  전달한다. `spark.jars.packages`와 arbitrary shell option은 v1에서 금지한다.
- output proof에는 `run_id`, revision, input version, output snapshot ID, row count,
  schema hash, plan hash, started/finished timestamp를 포함한다.
- worker가 `OutputProof`를 쓰지 못하면 성공 exit code여도 coordinator는 run을
  `lost`로 두고 publish 성공으로 선언하지 않는다.

### 5.5 Ballista 상태 전이

```text
planned ──submit──> submitted ──driver running──> running ──proof──> succeeded
   │                    │              │                         │
   │                    │              ├──> failed                └─ immutable result
   │                    └──> lost (coordinator crash) ──reconcile──┘
   └──> cancelled
```

`lost`는 실패가 아니라 “coordinator가 결과를 모른다”는 상태다. `reconcile`만
`lost → running|succeeded|failed`로 바꿀 수 있다. 이 구분이 없으면 네트워크 단절 뒤
이미 실행 중인 Spark job을 중복 제출하게 된다.

## 6. 하위호환성과 migration 정책

### 6.1 YAML 정책

- 모든 기존 pipeline/transform YAML은 v0.2와 동일하게 parse·실행되어야 한다.
- `PipelineSpec.stream: StreamSpec | None = None`, `TransformSpec.execution` 또는
  profile reference는 optional default다. Pydantic `extra="forbid"` 원칙은 유지한다.
- 새 spec에는 optional `api_version: "v1"`을 허용하되, 미기재 기존 파일은 v1으로
  해석한다. v2가 필요해질 때까지 migration warning을 내지 않는다.
- P2 field 추가 시 Pydantic model → JSON schema → generated reference 문서 순서로
  갱신하고 `scripts/gen_schema_docs.py --check`를 CI gate로 유지한다.
- `pugio run`/`gladius run`의 기존 command·출력·exit code는 보존한다. 새 기능은
  `hasta`/`ballista` 명령 또는 명시적 `--executor`로 opt-in한다.

### 6.2 state와 artifact 정책

- State DB migration은 forward-only며 backup을 남긴다. migration failure는 원 DB를
  변경하지 않고 중단한다.
- checkpoint reset, artifact cleanup, Iceberg expire/snapshot delete는 destructive
  command로 분리하고 dry-run + explicit confirm을 요구한다.
- run manifest, batch ledger, DLQ, output proof는 audit 보존 기간 동안 삭제하지 않는다.
  Onager maintenance가 삭제하기 전에는 catalog/reference 검증을 한다.
- spec revision hash가 달라지면 기존 stream checkpoint를 조용히 재사용하지 않는다.
  operator가 `--adopt-checkpoint`를 명시하고 compatibility check를 통과해야 한다.

## 7. 실패·재시작 매트릭스

### 7.1 Hasta

| 주입 시점/사건 | 기대 상태 | 재시작 결과 | 필수 검증 |
|---|---|---|---|
| poll 전 crash | checkpoint C0 | C0에서 동일 poll | 데이터 write 0 |
| poll 후, batch ledger 전 crash | C0, batch 없음 | 다시 poll, 결정적 batch 생성 | 중복 없음 |
| sink write 중 crash | C0, batch running/failed | 같은 batch ID 재write | visible partial 0 |
| sink 성공 후 checkpoint 전 crash | C0, output 존재 | 같은 batch ID 재write 후 C1 commit | 결과 중복 0, checkpoint 1회 |
| checkpoint commit 뒤 crash | C1, committed | 다음 poll은 P1부터 | 이전 batch 재fetch 0 |
| lease 만료/다른 runner | stale runner commit 거부 | 새 owner가 reconcile | fence 위반 0 |
| contract/schema 위반 | quarantined 또는 failed | policy에 맞춰 진행/중단 | 정상 sink 오염 0 |
| late event | policy별 metric/DLQ | watermark는 후퇴하지 않음 | late count·reason 보존 |
| SIGTERM | 새 poll 정지 | safe commit 또는 다음 run retry | batch 손실/중복 0 |

### 7.2 Ballista

| 주입 시점/사건 | 기대 상태 | reconcile 행동 | 필수 검증 |
|---|---|---|---|
| manifest 전 crash | run 없음 | submit 가능 | remote job 0 |
| manifest 후 submit 전 crash | planned | 동일 run ID로 submit | manifest 내용 불변 |
| submit 후 job handle 기록 전 crash | lost | deterministic pod name으로 검색 | 중복 job 0 |
| coordinator 연결 단절 | lost | active job 관찰 재개 | output 중복 publish 0 |
| Spark driver/executor failure | failed | explicit retry만 새 attempt | failed output 비가시 |
| worker 성공, proof 전송 실패 | lost | staging proof 확인 | 성공/실패 오판 0 |
| output commit 후 coordinator crash | lost | snapshot/proof 확인 후 succeeded | snapshot 1개만 visible |
| user cancel | cancelling/cancelled | remote cancel 확인 | 후속 checkpoint/publish 0 |
| input version 변경 | preflight fail | 새 run 필요 | mixed input 0 |

이 매트릭스의 모든 행은 sleep 기반 통합 테스트가 아니라 deterministic fault injection
hook으로 재현한다. 실제 cluster kill 테스트는 nightly/release candidate에서 한 번 더
실행한다.

## 8. 테스트 전략과 DoD

### 8.1 테스트 계층

| 계층 | 위치 | 외부 의존성 | 목적 |
|---|---|---|---|
| 단위 | 각 패키지 `tests/` | 없음 | ID, transition, cursor, canonical hash, parser, capability gate |
| 통합 | `tests/integration/` | SQLite/DuckDB, mocked HTTP | Pugio sink bridge, stream commit, local logical-plan equivalence |
| 컨테이너 | `tests/integration/` opt-in | Postgres + MinIO | outbox CDC, DB transaction, object artifact proof |
| Spark smoke | `tests/spark/` opt-in | local Spark container | portable plan local-vs-Spark golden output |
| Kubernetes E2E | `tests/k8s/` scheduled | kind + Spark image + MinIO/Iceberg catalog | submit/status/cancel/reconcile/crash recovery |
| release canary | 별도 workflow | staging cluster | 실제 RBAC, image, catalog, cost evidence |

PR의 기본 CI는 기존 3 OS 빠른 suite와 type/lint/schema gate를 유지한다. Docker/Spark/Kubernetes는
Linux 전용 opt-in label 또는 nightly로 분리하되, release 이전에는 모두 green이어야 한다.

### 8.2 핵심 테스트 목록

**P2-0**

- State migration: 빈 DB, v0.2 DB, 중간 migration 실패, backup 복원, newer-version 거부.
- lease: acquire/renew/expire/takeover, stale fencing token commit 거부, two-thread 경쟁.
- artifact capability: 요구 보장보다 약한 parquet/object sink와 profile을 preflight 거부.
- canonical revision/batch/run identity: key 순서·clock·process 재시작과 무관한 결정성.

**Hasta**

- 100개 microbatch, 각 1,000 event의 kill-and-resume: `count = distinct(event_id) = 100,000`.
- sink 성공 직후 process crash, checkpoint commit 직후 crash, lease takeover 각각.
- REST cursor overlap/empty page/reordered response, rate-limit/auth refresh.
- outbox insert/update/delete, same event 재전달, gap/sequence regression의 fail-fast.
- table cursor 같은 timestamp tie-breaker, delete unsupported preflight, timezone validation.
- every `on_late` policy 및 DLQ artifact/schema contract propagation.

**Ballista**

- 각 portable operation 조합을 DuckDB와 Spark가 같은 canonical Arrow table로 내는 golden
  suite. row order는 비교하지 않고 schema·key-sorted row·null semantics를 비교한다.
- DuckDB-only SQL/UDF/function/type이 remote submit 전에 거부되는 test.
- immutable input snapshot이 없는 URI와 non-atomic output을 preflight 거부하는 test.
- manifest, pod name, external handle recovery; submit 후 fault injection → reconcile.
- Spark driver failure, executor loss, worker output proof corruption, cancellation.
- Iceberg snapshot 한 번 publish, concurrent writer conflict resolution, stale run denial.

### 8.3 package별 Definition of Done

#### P2-0 — 공통 기반

- [ ] schema migration runner와 backup/forward-only policy가 구현·문서화됨.
- [ ] `runs`, `leases`, `stream_checkpoints`, `stream_batches`가 state API로만 접근됨.
- [ ] lease/fencing·batch identity·artifact capability의 unit/속성 기반 tests가 있음.
- [ ] 기존 v0.2 YAML과 StateStore resume 테스트가 변경 없이 통과함.
- [ ] Iceberg 또는 transaction sink의 `atomic_publish` capability integration test가 있음.
- [ ] `ruff`, `pyright`, schema doc check, 전체 pytest가 green이고 coverage 80% 이상.

#### P2-1 — Hasta polling microbatch

- [ ] `de-hasta`를 독립 설치/실행할 수 있고 `hasta run --once`가 동작함.
- [ ] REST poll source, Pugio parquet/DB sink bridge, contract/DLQ, metrics가 구현됨.
- [ ] stream write → checkpoint atomic transition과 crash matrix 10개 모두 테스트됨.
- [ ] 100,000-event deterministic resume test에서 중복·누락 0.
- [ ] status/checkpoint show/reset의 dry-run/confirm UX와 사용자 문서가 있음.
- [ ] default idle 비용은 foreground process 종료 시 0이며 daemon을 몰래 남기지 않음.

#### P2-2 — CDC v1

- [ ] Postgres outbox source가 ordered immutable event를 읽고 checkpoint를 안전히 전진시킴.
- [ ] I/U/D를 transactional Postgres/DuckDB sink에 적용하고 duplicate event도 결과가 불변.
- [ ] raw Parquet에는 `change_log`만 허용하고 current-state로 잘못 표기하지 않음.
- [ ] table-cursor source는 문서화된 제약을 validator와 warning으로 강제함.
- [ ] 10,000 event의 update/delete crash-recovery E2E가 DB final state와 정확히 일치함.

#### P2-3 — Ballista v1

- [ ] `de-ballista` base install은 cluster dependency를 설치하지 않으며 profile preflight가 비밀을 출력하지 않음.
- [ ] Gladius portable LogicalPlan v1과 local renderer가 기존 local output golden test를 유지함.
- [ ] Spark renderer가 portable corpus에서 local DuckDB 결과와 동치임.
- [ ] Kubernetes submit/status/cancel/reconcile이 stable run ID와 proof로 동작함.
- [ ] coordinator crash 직후 reconcile, driver failure, duplicate submit attempt에서 visible result가 하나임.
- [ ] v1 production output은 Iceberg/transactional capability 없이는 실행되지 않음.
- [ ] user guide에 local vs cluster 선택 기준, RBAC/image/storage requirements, honest limits가 있음.

#### P2-4 — 운영 완성/릴리스

- [ ] `arsenal run`이 stream/distributed feature 설치 여부를 감지해 정확한 설치·명령을 안내함.
- [ ] Scorpio가 stream lag, last checkpoint, rows/bytes, late/DLQ count, Ballista run/stage/cost metrics를 보여 줌.
- [ ] Spatha가 batch task와 `ballista reconcile` task를 의존성/priority/window에 포함할 수 있음.
- [ ] Onager가 active stream/run/staging artifact를 잠금 없이 정리하지 않음.
- [ ] Linux scheduled Kubernetes canary, release build, PyPI metadata, upgrade guide가 완료됨.
- [ ] 0.3 release checklist와 rollback/recovery runbook이 검토됨.

## 9. 벤치마크와 성능 기준

절대 수치를 지금 약속하지 않는다. 분산 환경의 비용·속도는 storage, cluster, data skew,
Spark version에 좌우되므로 기준 workload와 측정 방법을 먼저 고정하고 첫 accepted
baseline을 커밋한다. 성능 회귀만 수치 gate로 삼는다.

### 9.1 공통 기준

- 데이터 generator는 seed, schema, distribution, skew, input version을 기록한다.
- 결과에는 row count, key distinct count, schema hash, checksum을 함께 기록한다.
- warm/cold run, dataset URI, engine/image version, worker spec, storage 위치를 분리해
  기록한다.
- CI smoke는 correctness만 보며, 성능 비교는 고정된 self-hosted/Kubernetes runner에서
  실행한다.
- 같은 기준 환경의 accepted baseline 대비 p50 throughput 또는 cost/GB가 **20% 초과
  악화**하면 release blocker다. 원인을 기록하고 새 baseline은 승인된 변경에서만 갱신한다.

### 9.2 Hasta benchmark

| workload | 측정 | DoD |
|---|---|---|
| 1M events, 1 KiB/event, 1k/batch | ingest rows/s, checkpoint latency, p50/p95 end-to-end lag | correctness checksum과 함께 first baseline 기록 |
| restart after each Nth batch | duplicate/loss, replayed batches, checkpoint recovery time | 모든 N에서 duplicate/loss 0 |
| 10% late event | policy별 output/DLQ count, watermark lag | 정책 결과가 예상값과 완전 일치 |
| CDC 100k mixed I/U/D | final table checksum, DB transaction time | outbox final state와 정확히 일치 |

### 9.3 Ballista benchmark

| workload | 측정 | DoD |
|---|---|---|
| 10/100 GB Parquet, uniform | wall time, rows/s, executor CPU/memory, read/write bytes | DuckDB vs Spark crossover point를 수치로 문서화 |
| 100 GB join/aggregation, controlled skew | shuffle read/write, spill, max task duration, skew ratio | skew 지표와 tuning recommendation 기록 |
| 1 TB scale factor (release canary) | total cost, job retries, snapshot commit time | output proof/checksum과 cost/GB 기록 |
| fault during output commit | reconciliation time, visible snapshot count | snapshot 1개, no partial table |

`benchmarks/RESULTS.md`에는 표만 남기지 말고 raw JSON artifact의 URI/hash, runner
image, exact command, date, commit SHA를 함께 기록한다. "Spark가 더 빠르다"가 아니라
어느 규모부터 Ballista가 비용·시간상 타당한지를 사용자에게 보여 주는 것이 목표다.

## 10. 구현 work breakdown

각 task는 작은 atomic PR로 끝내며, 이전 task의 DoD가 green이기 전에는 다음 task를
시작하지 않는다. 버전은 P2 전반에서 `0.3.x`로 올리고 package dependency range를 함께
갱신한다.

### P2-0A — 설계 고정과 CI 정상화

1. 이 문서 검토/승인, ADR 3건(backend, commit capability, portable-plan boundary) 추가.
2. P2와 무관한 현재 Pyright failure를 소유 변경에서 해결하고 baseline CI를 green으로
   복원.
3. `docs/01-scope.md`, `08-limits.md`, README에 P2 status를 “구현 중”으로 바꾸지 않는다.
   각 실제 DoD가 통과한 뒤에만 갱신.

### P2-0B — StateStore v2와 실행 모델

1. `arsenal_core.execution` dataclasses/protocol/capabilities 추가.
2. forward migration/backup API, runs/leases/stream tables, deterministic clock injection 추가.
3. canonical JSON/spec revision hash, `LeaseToken`, `OutputProof` 구현.
4. state unit/property tests 및 v0.2 migration fixture 작성.

### P2-0C — commit capability와 Iceberg/transaction gate

1. Pugio sink에 `SinkCapabilities`를 추가하되 기존 `write()` 호출은 보존.
2. local Parquet, DuckDB, Postgres의 정확한 capability를 계약 test로 검증.
3. Iceberg sink/artifact committer를 optional extra로 구현하고 catalog concurrency 테스트.
4. raw object Parquet의 보장 수준을 `08-limits.md`에 명시하고 P2 preflight에서 강제.

### P2-1A — Hasta skeleton과 REST microbatch

1. `de-hasta` package, CLI, `StreamSpec`, source/sink protocol 생성.
2. REST poll cursor source, deterministic microbatch identity, `--once` loop 구현.
3. contract, schema drift, DLQ, Scorpio metric bridge 연결.
4. restart/lease/fencing fault injection suite 작성.

### P2-1B — 운영 UX

1. checkpoint/status/reset/replay-safe CLI와 JSON output.
2. SIGTERM graceful shutdown, lease release/expiry observability.
3. quickstart 및 fake REST example, reference schema 자동 문서화.

### P2-2 — CDC

1. Pugio/Postgres read adapter의 ordered outbox query와 event envelope validation.
2. transactional CDC apply sink; delete safety, schema validation, error classification.
3. 100k event DB integration + crash/retry test.
4. table cursor source와 limitation preflight/documentation.

### P2-3A — Gladius portable plan

1. current compiler regression golden tests를 고정한다.
2. typed `LogicalPlan v1`, SQLGlot expression parser, portability report 구현.
3. DuckDB renderer를 logical plan 경로로 이동하되 기존 `compile_sql()` public behavior를
   compatibility wrapper로 보존.
4. portable corpus와 local result equality tests 작성.

### P2-3B — Ballista and Spark worker

1. `de-ballista` profile loader, capability preflight, manifest/staging/ledger CLI.
2. Kubernetes submit/status/cancel adapter와 deterministic resource labels.
3. minimal Spark worker renderer, output proof, Iceberg commit integration.
4. kind/minio/catalog opt-in E2E 및 reconcile fault tests.

### P2-4 — umbrella·운영·release

1. Arsenal manifest dispatch, optional-extra UX, Spatha/Scorpio/Onager integration.
2. benchmark generator, reference environment, results, regression workflow.
3. upgrade/rollback/security/runbook, 3 OS lightweight CI + Linux nightly k8s workflow.
4. full release candidate, all DoD evidence, changelog, version compatibility table.

## 11. 설계 결정 기록(ADR 초안)

| ADR | 결정 | 근거 | 기각/보류 대안 |
|---|---|---|---|
| P2-ADR-1 | Hasta는 REST/outbox 폴링 microbatch부터 시작 | 기존 SQLite/cursor/retry를 재사용하며 idle infra가 0 | Kafka를 첫 소스로 넣으면 broker 운영·offset semantics가 core 계약 검증보다 앞섬 |
| P2-ADR-2 | CDC v1의 진실 원천은 append-only outbox | delete·순서·event identity를 명시적으로 제공 | timestamp-only table scan은 delete와 late update를 완전하게 표현 못 함 |
| P2-ADR-3 | stream checkpoint는 output commit 뒤 같은 DB transaction으로 전진 | crash 재시작에서 중복/누락을 막는 최소 원자 단위 | poll 직후 cursor advance는 sink 실패 시 데이터 유실 |
| P2-ADR-4 | Ballista는 logical plan, local-only escape hatch는 유지 | YAML 불변·backend 교체와 기존 power-user path를 동시에 만족 | DuckDB SQL 문자열을 Spark에 그대로 전달 |
| P2-ADR-5 | production Ballista v1은 Spark Kubernetes submit/reconcile | durable job identity, status/cancel, remote lifecycle을 관리 가능 | session 중심 Spark Connect를 제품용 job ledger로 과신 |
| P2-ADR-6 | remote concurrent publish는 Iceberg/transaction capability가 필수 | raw object rename의 비원자성을 숨기지 않음 | S3/GCS Parquet overwrite를 ACID처럼 표기 |
| P2-ADR-7 | state DB는 single-host coordinator only | SQLite WAL의 강점을 지키고 split-brain을 차단 | NFS 위 다중 host SQLite writer |

## 12. 출시 전 체크리스트

- [ ] 모든 P2 package의 README, `py.typed`, license, PyPI metadata, dependency range가
  release version과 일치한다.
- [ ] base install은 Spark/Kubernetes/Iceberg/Kafka dependency를 끌어오지 않는다.
- [ ] 모든 command가 secret, DSN, token, full manifest의 비밀 값을 로그/오류에 출력하지 않는다.
- [ ] `arsenal status`와 individual CLI가 guarantee level, checkpoint/run/job ID, recovery
  action을 명확히 보여 준다.
- [ ] 0.2 state/yaml upgrade fixture, 0.3 rollback boundary, DB backup recovery가 문서화됐다.
- [ ] full fault matrix, portability golden corpus, Kubernetes canary, benchmark evidence가
  release commit에 연결돼 있다.
- [ ] `docs/08-limits.md`가 구현한 것과 구현하지 않은 것(Kafka/log CDC/Flink/remote
  raw Parquet guarantees)을 정직하게 반영한다.

## 13. P2 시작 선언 조건

다음 다섯 조건을 모두 만족하면 코딩을 시작한다.

1. 이 설계의 product boundary, 첫 backend, storage guarantee 결정이 승인된다.
2. 현재 워킹트리의 P2 외부 Pyright failure가 해결되어 baseline CI가 green이다.
3. P2-0C가 쓸 test-only Postgres/MinIO/Iceberg 및 P2-3B가 쓸 Kubernetes canary의
   사용자 소유 인프라와 credential 방식을 확정한다.
4. `0.3.0`을 P2 foundation release로 삼는 package/version policy를 승인한다.
5. P2-0B부터 하나의 task/PR/DoD 단위로 진행하며, 다음 단계로의 건너뛰기를 허용하지
   않는다.

이 조건 뒤 첫 실제 구현은 **P2-0B: StateStore migration + execution/lease model**이다.
그것이 없으면 Hasta는 단지 무한 루프이고 Ballista는 단지 Spark submit wrapper가 된다.
