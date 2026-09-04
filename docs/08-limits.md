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
| 실시간 스트리밍 (Hasta) | P2 | Pugio/Gladius는 배치. Hasta는 폴링 마이크로배치(초~분)부터 시작하고, 밀리초급 실시간은 전용 인프라 없이는 P2 이후에도 불가함을 미리 밝혀둔다 ([07-cost-efficiency.md](07-cost-efficiency.md) "줄여주지 못하는 비용"). |
| 분산 실행 (Ballista) | P2 | 단일 노드 벡터화 밖(TB급·고동시성)은 처음부터 지원 범위 밖 — Executor 인터페이스로 백엔드 교체는 P2 계획이며, 그 인프라 비용은 사용자가 선택적으로 진다. |
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

## 무료의 경계

[10-direction.md](10-direction.md)의 "가입도 과금도 없는 데이터 플랫폼"이 정확히
무엇을 약속하고 무엇을 약속하지 않는지를 한 곳에 적는다. 무료 티어가 청구서로
바뀌는 경쟁 제품과의 차이는 기능이 아니라 **이 표를 먼저 보여준다는 것**이다.

| 항목 | 누가 내는가 | 약속의 성격 |
|---|---|---|
| Arsenal 도구 자체 (모든 패키지, 앞으로 나올 Augur 포함) | **아무도** | Apache-2.0. 계정·미터링·텔레메트리 없음. **영구적** — 코어에 과금이 붙는 일은 없다 |
| 컴퓨트 | 사용자의 노트북 | 위의 단일 노드 envelope가 한계선. 넘는 규모(Ballista, P2)는 사용자가 선택적으로 지불 |
| 로컬 스토리지 | 사용자의 디스크 | 오픈 Parquet. 반출 비용 0 |
| 클라우드 스토리지 (S3/GCS sink) | 사용자 | 선택 사항. 우리가 마진을 붙이지 않는다 |
| LLM 토큰 | 사용자 | 이미 구독 중인 Claude Code·Cursor 등이면 추가 비용 0. 로컬 모델(Ollama 등)이면 0. 특정 벤더에 묶지 않는다 |
| 스케줄러 | 사용자의 cron·GitHub Actions·기존 스케줄러 | 우리는 데몬을 띄우지 않는다 ([dogfooding.md](dogfooding.md)) |

**돈을 받게 된다면 어디서 받는가.** 실제로 비용이 발생하는 것, 예를 들어 호스팅된
스케줄러나 관리형 클라우드 실행만이 후보다. 로컬에서 도는 모든 것은 대상이 아니다.
이 문장을 지금 적어두는 이유는 "무료라더니"라는 배신감을 사전에 막기 위해서다.

## 관련 문서

- [10-direction.md](10-direction.md) — 무료 포지션과 AI 에이전트 층의 방향성
- [01-scope.md](01-scope.md) — P0/P1/P2 범위 확정과 마일스톤별 DoD
- [07-cost-efficiency.md](07-cost-efficiency.md) — "줄여주지 못하는 비용" (정직한 한계선의 비용 관점)
- [roadmap.md](roadmap.md) — 도구별 문제 정의와 P0/P1/P2 원본 로드맵
- [reference/packaging.md](reference/packaging.md) — 패키징·PyPI 이름 확인 노트
