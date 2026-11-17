# P2 완주 계획 — Hasta · Ballista를 0.4.0으로 출시하기까지

작성일: 2026-11-17
기준 커밋: 로컬 `main` = `f06d735`, 원격 `origin/main` = `62429ca`
검증 환경: macOS, `uv run pytest -q` / `ruff` / `pyright` 로컬 실행

---

## 0. 한 줄 요약

**남은 일은 "기능을 더 만드는 것"이 아니라 "이미 만든 P2 작업을 안전하게 착지시키는 것"이다.**
Hasta·Ballista·StateStore v2·Gladius LogicalPlan은 이미 구현되어 테스트가 통과하지만,
**커밋되지 않은 채 14커밋 뒤처진 베이스 위에 얹혀 있다.** 이 상태로 커밋하면 원격 `main`에
이미 들어간 보안·정합성 수정 두 건이 조용히 되돌아간다. 그 리베이스가 1순위다.

---

## 1. 측정된 현황 (추정 아님)

| 항목 | 측정값 |
|---|---|
| 전체 테스트 | `392 passed, 24 skipped`, coverage **84.27%** (게이트 80%) |
| `ruff check` | **1 error** — `packages/onager/tests/test_cli.py:12` (B009 `getattr` 상수 접근) |
| `pyright` | **1 error** — `packages/arsenal-core/src/arsenal_core/execution/canonical.py:39` (`reportUnknownVariableType`) |
| 미커밋 변경 | tracked 33개 수정 + untracked 20개 경로 (`packages/hasta/`, `packages/ballista/`, `arsenal_core/execution/`, `gladius/plan.py` 등) |
| 로컬 `main` ↔ `origin/main` | 로컬이 **14커밋 뒤처짐**, 앞선 커밋 0 |
| 겹치는 파일 | 22개 (원격에서도 바뀌고 로컬에서도 수정됨) |
| 패키지 수 | 로컬 10개 / 원격 9개 — 로컬엔 `augur` 없음, 원격엔 `hasta`·`ballista` 없음 |
| 스택 | stash 없음 — **P2 작업의 유일한 사본이 워킹트리다** |

### 1.1 원격 `main`이 이미 가진 것 (로컬엔 없음)

- `0.3.0` 정식 릴리스 (2026-08-14, CHANGELOG 기록 완료)
- 적대적 테스트로 찾은 **검증된 결함 5건 수정** (`fix/five-verified-defects`, PR #12)
- **`de-augur`** — 9번째 패키지, schema-RAG text-to-SQL + 9분류 eval harness (PR #13)
- `docs/10-augur-eval-protocol.md`, `release.yml`의 `de-augur` 등록

### 1.2 로컬 워킹트리가 이미 가진 것 (원격엔 없음)

| 영역 | 상태 |
|---|---|
| `arsenal_core/execution/` | 엔진 중립 실행 모델·protocol·capability·canonical hash — 완료 |
| StateStore v2 | forward migration, backup, fenced lease, run/stream batch·checkpoint 커밋 — 완료 |
| `gladius/plan.py` | 이식 가능 LogicalPlan v1 + portable preflight, `compile_sql()` 호환 — 완료 |
| `packages/hasta` | REST 폴링 마이크로배치, Postgres outbox CDC(I/U/D), lease/checkpoint — 완료 |
| `packages/ballista` | profile preflight, immutable manifest/proof, Spark K8s lifecycle, worker 경계 — v1 skeleton |
| `pugio/sinks/contracts.py`, `cdc.py` | sink capability 선언 + 레거시 API 보존 어댑터 — 완료 |
| `schemas/stream.json`, `schemas/ballista-profile.json` + reference 문서 | 생성 완료 |

---

## 2. 1순위 위험 — stale base 리베이스

겹치는 22개 파일 중 **최소 2건은 의미 충돌**이다. 지금 그대로 커밋하면 원격의 수정이 사라진다.

### 위험 A — Parquet sink 동시성·Windows 수정 소실

`origin/main`은 `packages/pugio/src/pugio/sinks/parquet.py`에
`_tmp_path()`(pid+uuid 고유 임시 파일)와 `_replace_with_retry()`(Windows 공유 위반 재시도 →
`RetryableError`)를 추가했다. 로컬 워킹트리의 같은 파일은 **`capabilities` 한 줄만 추가한
구버전**이다. 그대로 커밋하면 동시 writer가 같은 임시 파일에 섞어 쓰는 결함이 되살아난다.

### 위험 B — SQL 인젝션 하드닝 소실

`origin/main`은 transpiler의 `map`/`derive`/`filter` 표현식을 괄호로 감쌌다
(`({expr}) AS ...`, `WHERE ({cond})`). 여분의 `)`가 `COPY` 래퍼를 닫고 탈출하는 것을 막는 수정이다.
로컬은 같은 파일을 **LogicalPlan 렌더러로 전면 재작성**했으므로, 그 괄호 하드닝을
`render_duckdb()` 경로에 **다시 적용해야** 한다. 단순 머지로는 복구되지 않는다.

### 위험 C — 버전 번호 오류

로컬 10개 패키지가 전부 `version = "0.3.0"`이다. 그런데 `0.3.0`은 **이미 출시된 버전**이다.
저장소 정책(CHANGELOG 서문)은 "0.x에서 breaking 축은 minor"라고 명시한다.
StateStore v2 마이그레이션은 breaking이므로 **P2는 `0.4.0`이어야 한다.**

---

## 3. 단계별 계획

각 단계는 원자적 PR 하나, 앞 단계가 green이 되기 전에는 다음을 시작하지 않는다.

### Phase 0 — 구조(救助): 작업 보존 (반나절) — ✅ 2026-11-17 완료

> 결과: `feat/p2-hasta-ballista` 브랜치, `origin/main` 위로 리베이스, 위험 A·B 복구 확인,
> 11개 패키지 0.4.0, ruff·format·pyright 0 error, `459 passed, 24 skipped`, coverage 85.47%.

> 목표: 워킹트리 유일본을 없애고, 원격 수정을 되살린 상태로 CI를 green으로 만든다.

| # | 작업 | 완료 기준 |
|---|---|---|
| 0.1 | `feat/p2-hasta-ballista` 브랜치를 현재 `main`(`f06d735`)에서 끊고, 미커밋 53개 경로를 **논리 단위 6커밋**으로 분리 커밋 (core/execution → state v2 → gladius plan → pugio contracts → hasta → ballista) | `git status` clean, 각 커밋이 단독으로 테스트 통과 |
| 0.2 | `git fetch && git rebase origin/main` — 22개 겹침 파일 충돌 해결 | 충돌 0, 빌드 통과 |
| 0.3 | **위험 A 복구**: `parquet.py`의 `_tmp_path`/`_replace_with_retry`를 유지한 채 `capabilities` 재적용 | `test_parquet_sink_concurrency.py` 통과 |
| 0.4 | **위험 B 복구**: 괄호 하드닝을 `render_duckdb()`와 `plan.py` 표현식 경로에 재적용 | `test_expression_injection.py`가 새 경로에서 통과 |
| 0.5 | **위험 C 수정**: 11개 패키지(`augur` 포함) 버전을 `0.4.0`으로, 내부 의존 범위를 `>=0.4,<0.5`로 정렬, `uv lock` 재생성 | `scripts/check_release_metadata.py` 통과 |
| 0.6 | CI red 2건 수정: `onager/tests/test_cli.py` B009, `canonical.py:39` pyright | `ruff check` · `ruff format --check` · `pyright` 모두 0 error |
| 0.7 | 머지된 `augur` + 새 `hasta`/`ballista`로 전체 회귀 | `pytest` green, coverage ≥ 80% |

**게이트:** 이 7개가 전부 green이기 전엔 Phase 1을 시작하지 않는다. (설계문서 §13 조건 2와 동일)

---

### Phase 1 — 설계자가 의도적으로 남긴 구멍 메우기 (3~5일)

> P2-0C·P2-3B에서 "범위 밖"으로 남긴 것들. 이게 없으면 Ballista는 Spark submit 래퍼다.

| # | 작업 | 현재 상태 | 완료 기준 (설계문서 §8.3 DoD) |
|---|---|---|---|
| 1.1 | **실제 blob store 어댑터** | `InMemoryBlobStore`만 존재 | S3/GCS/MinIO `create`(if-none-match)·`read` 구현, manifest/proof 불변성 테스트가 실제 객체 스토리지에서 통과 |
| 1.2 | **실제 cluster probe** | `UnverifiedEnvironmentProbe`만 존재 | `kubectl`/K8s API로 이미지·RBAC·storage 접근 확인, 실패 시 비밀값 노출 없이 PreflightError |
| 1.3 | **Iceberg committer 구현** | optional extra와 capability 게이트만 선언 | `TransactionalOutputCommitter` 구체 구현 + catalog 동시성 테스트. 없으면 production 실행 거부 (ADR-6) |
| 1.4 | **opt-in E2E**: kind + MinIO + catalog | 없음 | Linux nightly 워크플로에서 submit→status→reconcile→commit 1회 통과 |
| 1.5 | **portable corpus 동치성** | Spark 렌더러(`apply_spark_plan`)는 있으나 동치 corpus 없음 | 동일 plan이 DuckDB와 Spark에서 같은 결과(정렬 비의존)임을 corpus로 증명 |

---

### Phase 2 — P2-1B 운영 UX (2~3일)

| # | 작업 | 현재 상태 | 완료 기준 |
|---|---|---|---|
| 2.1 | `hasta checkpoint show` / `reset` (dry-run + confirm) | `run`/`status` 2개뿐 | JSON 출력, 파괴적 명령은 명시 확인 필요 |
| 2.2 | SIGTERM graceful shutdown, lease release/expiry 관측 | 미확인 | 종료 시 데몬 잔류 0, lease 즉시 해제 |
| 2.3 | quickstart + fake REST example | 없음 | `examples/`에 실행 가능한 스트림 예제 1개 |
| 2.4 | 10만 이벤트 결정적 resume 테스트 | Docker 게이트로 skip 중 | 중복·누락 0을 CI(Docker 가용 job)에서 증명 |

---

### Phase 3 — P2-4 통합 (3~4일)

> 지금 `arsenal` 우산 CLI에 `hasta`/`ballista` 참조가 **0건**이다. 사용자는 새 기능에 닿을 수 없다.

| # | 작업 | 현재 상태 | 완료 기준 |
|---|---|---|---|
| 3.1 | Arsenal manifest dispatch + optional-extra 안내 | 참조 0 | `arsenal run`이 stream/distributed 설치 여부를 감지해 정확한 설치 명령 안내 |
| 3.2 | Scorpio 스트림 메트릭 | `freshness.py` 61줄뿐 | stream lag, last checkpoint, rows/bytes, late/DLQ count, Ballista run/stage/cost 노출 |
| 3.3 | Spatha에 `ballista reconcile` 태스크 | 없음 | 의존성·priority·window에 포함 가능 |
| 3.4 | Onager 안전장치 | 미확인 | active stream/run/staging artifact를 잠금 없이 정리하지 않음 |
| 3.5 | `release.yml`에 `de-hasta`·`de-ballista` 등록 | 누락 | bootstrap 선택지와 prefix 매핑 추가 |
| 3.6 | Hasta/Ballista 벤치마크 베이스라인 | `bench_transform.py`만 | `benchmarks/RESULTS.md`에 첫 accepted baseline 커밋 (회귀만 게이트) |

---

### Phase 4 — 0.4.0 릴리스 (1~2일)

설계문서 §12 체크리스트를 그대로 사용한다.

- [ ] README·README.ko의 Hasta/Ballista 상태를 `📋 planned` → 실제 상태로 갱신 (**DoD 통과 후에만** — §P2-0A 규칙 3)
- [ ] `docs/08-limits.md`에 구현한 것/안 한 것 정직하게 반영 (Kafka·log CDC·Flink·remote raw Parquet 보장 없음)
- [ ] CHANGELOG `0.4.0` 항목 + 0.3 → 0.4 upgrade guide (StateStore 마이그레이션 절차, rollback 경계)
- [ ] base install이 Spark/K8s/Iceberg/Kafka 의존성을 끌어오지 않음을 테스트로 확인
- [ ] 모든 명령이 secret·DSN·token을 로그/오류에 출력하지 않음을 확인
- [ ] 11개 패키지 README·`py.typed`·license·PyPI metadata 정합

---

### Phase 5 (선택) — 아직 손대지 않은 무기

> 상세 계획: [Pilum · Aquila 착수 계획](2026-11-17-pilum-aquila-plan.md) — Aquila 0.5.0, Pilum 0.6.0.

| 무기 | 상태 | 비고 |
|---|---|---|
| **Pilum** (reverse ETL) | 미착수 | P2. sink를 외부 끝점으로 일반화 + 멱등 키 + DLQ. Pugio sink 계약 재사용 가능 |
| **Aquila** (카탈로그·거버넌스) | 제안 단계 | Augur의 catalog 모듈과 겹침 — 먼저 경계 결정 필요 |
| **Augur** | experimental | 라이브 API 호출이 CI에서 미검증. 2주 eval 프로토콜 실행이 다음 단계 |

---

## 4. 권장 실행 순서와 소요

```
Phase 0 (반나절)  ← 지금 당장. 미루면 작업 유실 위험이 매일 누적
   ↓ CI green
Phase 1 (3~5일)   ← Ballista를 "제품"으로 만드는 유일한 구간
   ↓
Phase 2 (2~3일) ∥ Phase 3 (3~4일)   ← 서로 독립, 병렬 가능
   ↓
Phase 4 (1~2일)  → 0.4.0 출시
```

총 **9~14일** 규모. Phase 5는 0.5 이후로 분리한다.

## 5. 각 단계 검증 명령

```bash
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run python scripts/check_release_metadata.py
uv run python scripts/gen_schema_docs.py --check
uv run pytest -q
```

CI(`ci.yml`)가 3 OS × Python 3.11/3.12에서 이 순서 그대로 돌린다.
