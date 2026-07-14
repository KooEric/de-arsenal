# Draft 구현 인계 — 남은 작업 계획표

> **작성 시각:** 2026-07-12 (세션 중단 시점)
> **브랜치:** `feat/m1-draft` (아직 main 미병합)
> **실행 방식:** superpowers:subagent-driven-development (구현자 서브에이전트 → 태스크 리뷰 → 최종 브랜치 리뷰)
> **진행 원장:** `.superpowers/sdd/progress.md` (git-ignored). 재개 시 이 원장과 `git log`를 신뢰할 것.

이 문서는 draft 뼈대 구현 중 세션을 중단하면서 남긴 인계서다. 목표는 "모든 앱이
draft로 동작"이며, 수집(pugio)·변환(gladius)·우산 CLI(arsenal) 각각의 진입로를
실제로 실행 가능한 수준까지 채우는 것이다.

---

## P1 범위 잠금 재확인 (문서 기준선 — 이 인계서로 고정)

draft 구현(P0)을 이어받기 전, **P1 범위는 아래 세 항목으로 이미 확정·잠금**되어
있음을 이 인계서로 재확인한다. 이는 구현 대상이 아니라 **범위 계약**이며,
P0 코드가 P1의 자리(탈출구·discriminated union·sink 추상)를 침범하거나 닫지 않도록
하는 기준선이다. Task 2.14(실 API E2E 검증)의 "deferred"는 *네트워크 검증의 연기*일
뿐, 아래 P1 범위 잠금과는 무관하다 — **P1 범위 자체는 잠겨 있다.**

**P1 확정 항목 — `docs/01-scope.md` "P1 — 확장" 표:**
1. **dlt 소스 래퍼** (`type: dlt`) — 검증된 커넥터 수백 개 흡수 · `01-scope.md:104`.
   기반 메커니즘은 P0의 `type: python` 탈출구(Batch E에서 구현, `d180350`) 위에 선다.
2. **dbt 인터롭** — `arsenal.yaml`의 `- dbt: ./project` 실행 단계(dbt-duckdb 차용) ·
   `01-scope.md:105`. Batch F의 우산 CLI(`arsenal` 실행 파이프라인)가 이 단계 타입을
   받아들일 자리를 남겨둬야 한다.
3. **S3/GCS sink** — DuckDB httpfs 차용 · `01-scope.md:104`. P0의 Parquet sink 추상
   (`pugio/sinks/base.py`의 `Sink` 프로토콜)이 로컬 경로 가정에 하드코딩되지 않게
   유지할 것 — 이미 프로토콜은 경로 무관하게 설계됨.

**결정 근거 ADR 2건 — `docs/02-architecture.md` "설계 결정 기록" 표:**
- **ADR #6 「빌드-vs-차용: 차용 우선」** · `02-architecture.md:243` — 우리 소유 코드는
  신뢰성 코어·UX·게이트·글루 4가지뿐. DB 소스=DuckDB scanner, 커넥터 롱테일=dlt 래핑,
  SQL 생태계=dbt 인터롭. (위 P1 항목 1·2·3이 모두 이 원칙의 귀결.)
- **ADR #7 「탈출구는 P0부터 (`type: python`)」** · `02-architecture.md:244` — YAML 표현
  한계에서 사용자가 절벽에 떨어지지 않게. Batch E의 Python 커스텀 소스(`d180350`)가
  이 결정의 P0 이행이며, P1 dlt 래퍼의 토대다.

> **재개자 유의:** 위 표 행 번호(01-scope 104–105, 02-architecture 243–244)는 문서
> 수정 시 이동할 수 있다. 값이 아니라 **표 제목**("P1 — 확장", "설계 결정 기록")과
> 항목명으로 찾을 것.

---

## 전체 진행 현황

| # | 배치 | 내용 | 상태 |
|---|------|------|------|
| A | arsenal-core | 에러 분류·unit ID·StateStore·스펙 로더·재시도 | ✅ 완료 · 리뷰 통과 |
| B | pugio 코어 | REST 소스·Parquet 싱크·Runner (crash-resume 검증) | ✅ 완료 · 리뷰 통과 |
| C | CLI+예제 | `pugio run`/`status`·예제 YAML·README | ✅ 완료 · 리뷰 통과 |
| D | gladius draft | 변환 스펙·트랜스파일러·엔진·`compile`/`run`/`query` | ✅ 완료 · 리뷰 통과 (Critical SQL 이스케이프 수정 포함) |
| E | pugio 확장 소스 | FileSource·DatabaseSource(sqlite draft)·Python 탈출구 | ⚠️ **구현 완료·트리 그린, 그러나 리뷰 미진행** |
| F | arsenal 우산 CLI | 단일 진입점·레시피 3종·수집→변환 end-to-end | ⬜ 대기 |
| G | 최종 리뷰 | 전체 브랜치 코드 리뷰 + Minor 이슈 정리 | ⬜ 대기 |

**현재 트리 상태:** `uv run pytest -q` → 94 passed, 커버리지 94.63% (게이트 80%).
`ruff check`/`ruff format --check`/`pyright` 모두 통과(중단 직전 기준).

**M1(A·B·C)은 이미 DoD 충족** — "끊겨도 중복·누락 없이 재개되는 REST→Parquet
파이프라인"은 완성·검증된 상태로 확보돼 있다. D는 리뷰까지 통과. E는 코드는
들어갔으나 검증 게이트(태스크 리뷰)를 통과하지 않았다.

---

## ⚠️ Batch E — 재개 시 가장 먼저 처리할 것

Batch E 구현자는 작업을 사실상 끝내고 커밋까지 남겼으나 **태스크 리뷰를 받기 전에
세션이 중단**됐고, `batch-E-report.md`(구현 리포트)도 작성되지 못했다. 따라서 A~D와
달리 **E는 "완료"로 간주하면 안 되며, 재개 시 반드시 태스크 리뷰 1회를 거쳐야 한다.**

### E에서 실제로 일어난 변경 (git으로 확인한 사실)

커밋 범위: `76eab81..d180350`
- `0f89150` feat: file source — csv/jsonl/excel to arrow
- `aaac33f` feat: database source via sqlite3 driver with key-range units (draft)
- `d180350` feat: python custom source escape hatch via dynamic import

**M1 호환성에 영향을 주는 핵심 변경 (리뷰 최우선 확인 대상):**
1. `arsenal_core/spec/models.py`의 `SourceSpec`이 **discriminated union**으로 바뀜:
   `Annotated[RestSourceSpec | FileSourceSpec | DatabaseSourceSpec | PythonSourceSpec,
   Field(discriminator="type")]`. 기존 이름 `SourceSpec`은 union 별칭으로 유지되나,
   **`SourceSpec(type="rest", ...)` 직접 생성자 호출은 더 이상 불가** — `RestSourceSpec`을
   써야 한다. 이에 맞춰 M1 테스트(`test_rest_source.py`, `test_runner.py`, `test_spec.py`)가
   수정됐다. → **리뷰어는 이 union 전환이 M1의 계약(특히 로더의 friendly-error 경로와
   discriminator 누락 시 에러 메시지)을 깨지 않는지 확인할 것.**
2. `pugio/runner.py`의 소스 팩토리가 `spec.source.type`으로 분기하도록 확장됨
   (rest/file/database/python). → 분기 누락·잘못된 소스 생성 여부 확인.
3. `spec/loader.py`가 union 검증 에러를 다루도록 변경됨 → 어떤 필드가 문제인지
   여전히 이름을 대는지 확인.

**Draft 범위 결정(의도된 것):**
- 2.11 FileSource: csv/jsonl는 pyarrow로 구현·테스트. excel은 `fastexcel` extra가
  없으면 명확한 FatalError. `[project.optional-dependencies] excel`만 선언, 설치 안 함.
- 2.12 DatabaseSource: **psycopg/testcontainers가 아니라 내장 `sqlite3` 드라이버 기반
  draft.** postgres/mysql은 dialect로 선언만. 실제 운영 DB 커넥터는 후속 작업.
- 2.13 Python 탈출구: `module:ClassName` 동적 import, Source 프로토콜 미구현 시 FatalError.
- **2.14(실 API 5종 스펙 검증)는 네트워크 필요 → 이번 draft에서 제외(deferred).**

### E 재개 절차
1. 리뷰 패키지 생성:
   `.claude/plugins/cache/claude-plugins-official/superpowers/6.1.1/skills/subagent-driven-development/scripts/review-package 76eab81 d180350`
2. 태스크 리뷰 서브에이전트(sonnet) 투입 — 위 "M1 호환성 핵심 변경 3건"을 global
   constraints로 명시하고, `sqlite3 draft`·`excel 미설치`·`2.14 deferred`가 사전 승인된
   범위임을 알려줄 것.
3. Critical/Important 지적은 fix 서브에이전트로 처리 후 재리뷰. 통과하면 원장에
   `Batch E: review clean`으로 갱신.

---

## Batch F — arsenal 우산 CLI (대기)

**출처 계획:** `docs/plans/2026-07-08-m4-integration-release.md` Task 4.0 / 4.1 / 4.2
(브리핑 추출본은 `scratchpad/batch-F-brief.md`에 있으나 세션 스크래치라 다음 세션에는
없을 수 있음 — 위 플랜에서 다시 추출할 것: `sed -n '11,98p' 해당파일`).

- **Task 4.0 `arsenal` 우산 CLI — 단일 진입점:** `arsenal run/status` 등이 pugio·gladius를
  한 진입점에서 호출. `packages/arsenal/src/arsenal/cli.py`(현재 스텁)·`project.py` 구현.
  `[project.scripts] arsenal = "arsenal.cli:app"` 확인.
- **Task 4.1 원클릭 레시피 3종:** `packages/arsenal/src/arsenal/recipes/` 아래 실행 가능한
  레시피(예: github-issues 수집→변환). 현재 `github-issues/`만 스텁 존재.
- **Task 4.2 연계 예제 — 수집→변환 end-to-end:** pugio로 수집한 Parquet을 gladius로 변환하는
  실제 동작 예제. 이게 "모든 앱이 draft로 동작"의 최종 증거.

**주의:** Task 4.3(YAML 레퍼런스 자동 생성)·4.4(한계선 문서)·4.5(패키징)·4.6(릴리스)는
draft 범위 밖 — F에서는 4.0~4.2만.

**의존성:** F의 end-to-end 예제는 E의 소스들과 D의 gladius에 의존하므로, **E 리뷰를
먼저 끝낸 뒤 F에 착수**할 것.

---

## Batch G — 최종 브랜치 리뷰 (대기)

모든 배치 완료 후 `superpowers:requesting-code-review`의 whole-branch 리뷰를 **가장 강한
모델로** 1회 실행. 리뷰 패키지:
`scripts/review-package <merge-base> HEAD` (merge-base = `git merge-base main HEAD`).

**최종 리뷰에 반드시 넘길 누적 Minor 이슈 목록** (원장에서 발췌):
- Batch A: 커버리지 게이트 0→80 상향이 Task1 커밋에 묻어감(메시지 미반영, 내용 자체는 정당).
- Batch B: `runner.py`의 `except → mark_failed → raise` 실패 경로가 테스트 미커버(runner 93%).
- Batch C: `run`/`status`의 `except ArsenalError` 블록 DRY 여지, `examples/github-issues.yaml`의
  `(M1 완료 후 동작)` 주석이 이제 stale.
- Batch D: (Critical SQL 이스케이프는 fix+재리뷰 완료) — 잔여 Minor 없음.
- Batch E: **아직 리뷰 안 됨** — G 이전에 E 태스크 리뷰가 선행되어야 함.
- **P1 경계 확인:** 최종 리뷰는 위 "P1 범위 잠금 재확인" 절을 기준으로, P0 코드가
  P1 자리(dlt 래퍼용 `type: python` 탈출구, `- dbt: ./project` 단계 타입, httpfs sink용
  경로-무관 `Sink` 프로토콜)를 닫지 않았는지 확인할 것.

리뷰가 findings를 반환하면 **fix 서브에이전트 1개에 전체 목록을 넘겨** 처리(핑거당 1개
금지). 통과하면 `superpowers:finishing-a-development-branch`로 병합/PR 결정.

---

## 재개 빠른 체크리스트

```bash
# 1. 위치 확인
git branch --show-current          # feat/m1-draft 여야 함
git log --oneline main..HEAD       # 22개 커밋 (A~E)
cat .superpowers/sdd/progress.md   # 원장 — 어디까지 왔는지

# 2. 트리 그린 확인
uv run pytest -q                   # 94 passed 기대
uv run ruff check . && uv run ruff format --check . && uv run pyright

# 3. 순서: E 리뷰 → (필요시 fix) → F 구현+리뷰 → G 최종 리뷰 → 병합
```

**모델 라우팅(이 세션 규칙):** 구현·리뷰는 sonnet 서브에이전트로 진행하다가, 막히거나
문제가 생기면 fable로 전환해 해결한다. 최종 브랜치 리뷰(G)는 가장 강한 모델로.
