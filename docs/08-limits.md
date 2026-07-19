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
| 스키마 드리프트 감지+정책 | P1 | P0는 수집 시 **스키마 스냅샷 기록만** 한다(비교·경고·차단 정책 없음). [01-scope.md](01-scope.md) M2 절 "스키마 스냅샷 기록 (드리프트 감지·정책은 P1, 기록만 P0)" 참고. |
| 증분 변환 (`incremental: by_unit/by_key`) | P1 | Gladius는 현재 매 실행 전체 재계산. 증분 처리 설계는 [07-cost-efficiency.md](07-cost-efficiency.md) "증분 처리 전략" 절에 이미 스펙 초안이 있다. |
| dlt/dbt 인터롭 | P1 | `type: python` 탈출구(P0)가 dlt 래퍼의 기반 메커니즘이지만, `type: dlt` 소스와 `arsenal.yaml`의 `- dbt: ./project` 단계는 아직 없다 ([01-scope.md](01-scope.md) P1 표, ADR #6/#7 — `docs/plans/2026-07-12-draft-handoff.md`). |
| S3/GCS sink | P1 | 현재 sink는 로컬 경로(parquet/duckdb) 또는 직접 연결(postgres)만. `Sink` 프로토콜은 경로 무관하게 설계돼 있어 DuckDB httpfs 차용으로 확장할 자리는 이미 있다. |

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

## 관련 문서

- [01-scope.md](01-scope.md) — P0/P1/P2 범위 확정과 마일스톤별 DoD
- [07-cost-efficiency.md](07-cost-efficiency.md) — "줄여주지 못하는 비용" (정직한 한계선의 비용 관점)
- [roadmap.md](roadmap.md) — 도구별 문제 정의와 P0/P1/P2 원본 로드맵
- [reference/packaging.md](reference/packaging.md) — 패키징·PyPI 이름 확인 노트
