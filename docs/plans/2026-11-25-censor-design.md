# Censor 설계 (v0.7.0)

> 감찰관. 파이프라인과 데이터셋을 조사해 등급을 매기고, 고칠 곳을 근거와 함께 남긴다.

- 상태: 설계 승인 (2026-11-25), 구현 전
- 릴리스 대상: **v0.7.0** (신규 패키지 `de-censor`, minor)
- 참고: [whchoi98/harness-eval](https://github.com/whchoi98/harness-eval) (3단계 평가, 코드 채점, 파일 handoff, 성숙도 픽스처),
  [anthropics/knowledge-work-plugins `data`](https://github.com/anthropics/knowledge-work-plugins/tree/main/data)
  (`explore-data` 프로파일 항목, `validate-data` 분석 함정 목록), [Augur 평가 프로토콜](../10-augur-eval-protocol.md)

## 1. 목표와 범위

DE Arsenal 사용자가 "이 파이프라인과 데이터를 믿고 넘겨도 되는가"를 한 명령으로 확인한다. 결과는 같은
입력에 같은 등급을 내고, 실패한 항목마다 근거(파일·줄 또는 SQL·값)와 고칠 방법을 남긴다.

**평가 대상**

| 대상 | 예 | 적용 차원 |
|---|---|---|
| de-arsenal 프로젝트 (collect/transform YAML, `arsenal dataset` 작업) | `censor audit .` | 전부 |
| 파일 데이터셋 (Parquet, CSV, JSONL) | `censor audit --files 'data/*.parquet' --key id` | 데이터 상태, 신선도 |
| DuckDB 테이블 | `censor audit --duckdb .arsenal/dataset.duckdb --table published --key id` | 데이터 상태, 신선도 |
| Postgres 테이블 | `censor audit --table public.orders --postgres-dsn-env PG_DSN --key id --time-column created_at` | 데이터 상태, 신선도 |

**하지 않는 것**

- 범용 Claude Code 하네스 평가. harness-eval이 맡는다. Censor는 DE 특화 하네스 차원(에이전트 준비도)만 본다.
- dbt, Airflow 자체, 외부 웨어하우스 프로젝트 평가. Redshift, Hive 테이블 프로파일링 (v0.7.0 범위 밖).
- 파이프라인 재실행. Standard는 상태와 데이터를 읽기만 한다. 재실행은 외부 API를 부르고 데이터를 쓴다.
- 골든 케이스 자동 생성. Augur 케이스 파일이 없으면 질의 가능성은 "측정 안 됨"이다. 감점하지 않는다.
- 평가 대상의 수정. Censor가 쓰는 곳은 `.arsenal/censor/`뿐이다.

## 2. 구성 요소

```
packages/censor/          PyPI de-censor, CLI `censor`   측정 · 채점 · 이력 · 리포트 (결정적)
packages/arsenal/         `arsenal eval` = censor audit 별칭 (지연 import)
plugins/de-arsenal/skills/evaluate/SKILL.md            CLI 실행 · 결과 설명 · Full 리뷰 서브에이전트
schemas/censor-report.json                              stdout JSON 계약
```

- `de-censor` 의존: `arsenal-core`, `de-gladius`(compile), `pugio`(상태·DLQ 읽기), `duckdb`. `de-augur`, `de-aquila`는
  extra(`de-censor[augur]`, `de-censor[catalog]`)로 둔다. 없으면 해당 체크는 na와 설치 안내.
- `de-arsenal`은 Censor에 직접 의존하지 않는다. 새 extra `de-arsenal[eval]` = `de-censor`. `arsenal eval`은 import 실패 시
  `pip install 'de-arsenal[eval]'`을 안내하고 종료 코드 2.
- `arsenal eval [ARGS]`와 `censor audit [ARGS]`는 같은 인자, 같은 출력, 같은 종료 코드를 낸다.

**CLI**

```bash
censor audit [DIR] [--tier quick|standard] [대상 옵션] [규모 옵션] [--lang en|ko] [--json-only]
censor report [--limit N]                 # 실행별 등급·차원 점수 추이
censor compare <run_a> <run_b>            # 상태가 바뀐 체크만
censor attach-findings <run_id> FILE      # Full 리뷰어 지적을 run에 붙임 (등급 불변)
```

`--tier`의 기본값은 `standard`다. `full`은 CLI 값이 아니다. Full은 스킬이 Standard 실행 위에 리뷰를 얹는 흐름이다.

## 3. 평가 차원

| 차원 | 측정 근거 | 단계 | 기본 가중치 |
|---|---|---|---|
| 정확성 | `arsenal check`, `gladius compile` 통과 | Quick | 20 |
| 품질 가드 | `validate` 규칙 커버리지(키 컬럼 not_null/unique), `on_violation` 명시 여부, `schema_drift`, `contract` | Quick | 15 |
| 보안 | DSN·토큰을 `*_env`로 받는지, 스펙에 평문 비밀값·자격 증명 URL이 없는지 | Quick | 15 |
| 운영성 | 스케줄 정의(Airflow DAG 등), aquila lineage 가능 여부, 프로젝트 README | Quick | 5 |
| 에이전트 준비도 | de-arsenal 플러그인·스킬 사용 가능, CLAUDE.md에 `arsenal check`/`run` 명시, 비밀값 검사 훅이 `*.yaml`을 포함, `.arsenal/` 삭제 deny 규칙 | Quick | 5 |
| 신뢰성 | 실패 unit 수, DLQ 비율, 실패 후 마지막 publication 유지 | Standard | 15 |
| 신선도 | `arsenal status`, `--max-age-hours` 설정, 마지막 성공 시각 | Standard | 10 |
| 데이터 상태 | 행 수, 키 유일성, 컬럼별 NULL 비율, 데이터 기간(min/max of time column) | Standard | 15 |
| *질의 가능성 (별도)* | `augur eval` CORRECT 비율, provider·model 함께 기록 | Standard (`--augur-cases` 명시 시) | 등급 제외 |

- 데이터셋 대상(파일·DuckDB·Postgres)에는 데이터 상태와 신선도만 적용한다. 나머지는 na다.
- 데이터 상태 항목은 data 플러그인 `explore-data`의 프로파일(grain, 기본 키 유일성, NULL 비율, 기간)을 따른다.
- 질의 가능성은 `--augur-cases FILE`을 명시할 때만 돈다. 케이스 파일이 있다는 이유만으로 LLM을 부르지 않는다(CI에서
  예고 없는 API 비용 방지). 케이스 파일이 있는데 옵션이 없으면 리포트에 "측정하려면 `--augur-cases`" 안내만 남긴다. Augur 실패 모드 중 RETRIEVAL_MISS,
  HALLUCINATED_COLUMN은 "컬럼·테이블 이름이 질문과 동떨어짐", WRONG_RESULT·WRONG_SHAPE는 "집계 기준 불분명(중복·단위)"
  지적으로 리포트에 옮긴다. NON_SELECT·EXEC_ERROR는 모델 쪽 문제이므로 옮기지 않는다.

## 4. 채점

**체크**: `id`, `dimension`, `weight`, `status`(pass | warn | fail | na | error), `blocker`(bool), `evidence`, `fix`.

- 차원 점수(0~10) = 10 × (pass 가중치 + 0.5 × warn 가중치) ÷ (pass·warn·fail 가중치 합). na와 error는 분모에서 뺀다.
- 적용 체크가 하나도 없는 차원은 na이고, 종합 점수의 분모에서 뺀다.
- 종합 점수 = 차원 가중치로 가중 평균(0~100).
- 등급 기준표는 `censor/grade.py` 한 곳: A+ ≥ 95, A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, F < 60.
- 차단 체크(`blocker=true`)가 하나라도 fail이면 등급 상한은 C다. 차단 체크: 스펙의 평문 비밀값, `arsenal check` 실패,
  transform compile 실패.
- 등급은 Quick·Standard 체크만으로 계산한다. Augur 정확도와 Full 리뷰 지적은 리포트에 나란히 보여줄 뿐 등급을 바꾸지 않는다.

## 5. Full 흐름 (스킬)

1. `censor audit --tier standard`를 실행하고 run_id를 얻는다.
2. 서브에이전트 두 명을 병렬로 띄운다. 입력은 `.arsenal/censor/runs/<run_id>.json`과 스펙 경로(파일로 전달).
   - transform 리뷰어: `validate-data` 함정 목록(조인 폭증, 평균의 평균, 타임존 불일치, 분모 변경, 기간 비교 불완전)으로
     transform SQL을 본다.
   - 가드 리뷰어: 측정된 분포(min/max, NULL 비율, 중복)와 `validate` 규칙이 맞는지 본다.
3. 각 리뷰어는 `schemas/censor-findings.json` 형식으로 `findings.json`을 낸다(`severity`, `file`, `line`, `summary`, `fix`).
4. `censor attach-findings <run_id> findings.json`으로 붙인다. 형식이 틀린 지적은 그 지적만 거부하고 run은 유지한다.
5. 스킬은 리포트를 사용자의 언어로 풀어 설명한다: 등급 → 가장 먼저 고칠 3가지 → 나머지.

## 6. 규모 보호 (큰 데이터·많은 파일)

Quick은 스펙만 읽으므로 규모와 무관하다. Standard는 데이터를 읽기 전에 규모를 잰다.

**사전 측정 (데이터를 읽지 않음)**

- 파일: glob 결과 파일 수와 `stat` 크기 합.
- Postgres: `pg_class.reltuples`(추정 행 수), `pg_total_relation_size`. 카탈로그 조회 한 번.
- DuckDB: `duckdb_tables()`의 `estimated_size`.

**기준 (이름 있는 상수, `censor/limits.py`)**

| 상수 | 기본값 |
|---|---|
| `MAX_FILES` | 2,000 |
| `MAX_BYTES` | 5 GB |
| `MAX_ESTIMATED_ROWS` | 50,000,000 |

기준을 넘으면 데이터 상태 체크를 건너뛰고(na, 사유 기록) 다음 안내를 stderr와 리포트에 낸다. 다른 차원은 그대로 평가한다.

```
data/raw/*.parquet: 18,432 files, 41.2 GB (estimated 620M rows).
Profiling everything would take a long time and a lot of memory, so it was skipped.
Pick one:
  --sample 1%                 profile a sample only (key uniqueness is reported as "found in the sample")
  --since 2026-11-01          only this period (needs --time-column)
  --files 'data/raw/dt=2026-11-*/*.parquet'   narrow to recent partitions
  --full                      read everything (memory limit 4GB, spills to disk)
Quick and the other dimensions were evaluated as usual.
```

**실행 중 보호**

- DuckDB `memory_limit`(기본 4GB, `--memory-limit`), `temp_directory`(`.arsenal/censor/tmp/`)로 디스크 spill.
- 쿼리마다 `--timeout`(기본 120초). Postgres는 `statement_timeout`도 같은 값으로.
- 시간 초과 체크는 fail이 아니라 na와 사유(`timeout 120s`).

**표본 결과의 표기**

- 표본에서 중복 키 발견 → fail (실제로 있다).
- 표본에서 중복 없음 → warn, "표본 기준 통과" (전체 유일성의 증명이 아니다).
- 리포트 헤더에 표본 비율과 방식을 적는다.

**구현 전 확인 항목**: Postgres 큰 테이블은 `TABLESAMPLE SYSTEM`을 서버 쪽에서 실행해야 전체 행이 네트워크로 오지 않는다.
DuckDB postgres 확장의 `postgres_query()`로 이를 내려보낼 수 있는지, `READ_ONLY` ATTACH에서도 되는지 먼저 검증한다.
안 되면 Postgres 표본은 `--since`(시간 컬럼 필터의 pushdown)만 지원하고 `--sample`은 거부한다.

**스킬 가이드**: 처음 보는 큰 데이터는 `--sample 1%`로 시작하고, 문제가 된 차원만 `--since`로 좁혀 다시 본다.

## 7. 출력·이력·리포트

- stdout: JSON (`schemas/censor-report.json`). stderr: 사람이 읽는 요약. `--json-only`면 stderr 요약 생략.
- 종료 코드: 0 = fail 없음, 1 = fail 또는 error 체크 있음, 2 = 도구 오류(대상 없음, 접속 실패, 이력 저장 실패).
- 이력: `.arsenal/censor/runs/<run_id>.parquet`(체크 1개 = 1행)와 `<run_id>.json`(리포트 원본). 데이터셋 대상도 현재
  디렉터리의 `.arsenal/censor/`에 쓴다. `gladius query`로 바로 집계할 수 있다.
- 리포트: `.arsenal/censor/reports/<run_id>.md`, `--lang en|ko`(기본 en). 순서: 등급 → 차원표 → fail·warn 체크(고칠 방법)
  → 질의 가능성 → 리뷰 지적 → 규모·표본 안내.
- run_id: `YYYYMMDD-HHMMSS-<tier>`.
- `.arsenal/censor/`는 처음 쓸 때 `.gitignore`(`*`)를 만든다. 이미 있으면 건드리지 않는다.

## 8. 오류 처리

| 상황 | 처리 | 종료 코드 |
|---|---|---|
| 체크 하나가 예외 (스펙 파싱, SQL 오류) | 그 체크만 `error`와 원인. 분모 제외, 나머지 계속 | 1 |
| 시간 초과, 규모 기준 초과 | `na`와 사유 | 결과에 따름 |
| Postgres 접속·인증 실패, 테이블 없음 | 데이터 차원 na, Quick 체크는 그대로. 메시지에 DSN 없음 | 2 |
| 대상 없음 (경로 없음, 프로젝트도 데이터셋 인자도 아님) | 줄 수 있는 인자를 한 줄로 안내 | 2 |
| 다른 실행이 `.arsenal/` lease 보유 | 기다리지 않음. "실행 중이라 상태가 바뀌는 중" warn | 결과에 따름 |
| Augur 키 없음, LLM 호출 실패 | 질의 가능성 "측정 안 됨". 등급 영향 없음 | 결과에 따름 |
| 이력 저장 실패 (권한, 디스크) | stdout JSON은 내고 stderr에 저장 실패 알림 | 2 |

- 오류는 `arsenal_core.errors.FatalError`. 체크 내부 예외는 러너가 잡아 `CheckResult(status="error")`로 바꾼다.
- 읽기 전용: Postgres는 `ATTACH ... (TYPE POSTGRES, READ_ONLY)`(pugio `sources/database.py`와 같은 방식), DuckDB 파일은
  `read_only=True`로 연다.

## 9. 테스트

저장소 규약: pytest, `--cov-fail-under=80`, Postgres는 testcontainers + `importorskip`(`pugio/tests/test_postgres_sink.py` 방식).

1. **단위**: 체크마다 pass·warn·fail 픽스처. `grade.py` 경계값과 차단 체크의 C 상한. na·error 분모 제외, warn 0.5.
   규모 기준 상수 경계와 안내 문구.
2. **성숙도 픽스처**: `packages/censor/tests/fixtures/{minimal,functional,robust,production}`. 각 기대 등급을 고정하고
   minimal < functional < robust < production 순서를 검증한다(채점 기준 변경의 회귀 장치).
3. **통합**: Parquet 픽스처(중복 키, NULL, 기간), `.arsenal/dataset.duckdb`의 `published`, testcontainers Postgres(중복 키 →
   fail, 추정 행 수로 규모 기준, `statement_timeout`).
4. **읽기 전용 보장**: 평가 전후 대상 파일 해시, DuckDB 파일 해시, Postgres 행 수가 같다.
5. **CLI·별칭**: 종료 코드 0/1/2, stdout JSON을 `schemas/censor-report.json`으로 검증, `arsenal eval` ≡ `censor audit`,
   Censor 미설치 시 `arsenal eval`의 설치 안내.
6. **e2e**: `tests/e2e/test_recipes.py`에 `arsenal init csv-cleanup` → `arsenal run` → `censor audit .`의 기대 등급 추가.
7. **스킬**: Full 서브에이전트는 자동 테스트하지 않는다. `attach-findings`의 findings 스키마 검증과 잘못된 지적의 개별 거부를
   단위 테스트로 고정한다.

## 10. 릴리스 (v0.7.0)

- **minor인 이유**: 새 PyPI 프로젝트 `de-censor`, 새 명령 `arsenal eval`, 새 extra `de-arsenal[eval]`.
- 14개 패키지 모두 0.7.0으로 올리고 교차 핀을 `>=0.6,<0.7`에서 `>=0.7,<0.8`로 바꾼다.
  `scripts/check_release_metadata.py`는 패키지 수를 세기만 하므로 수정이 필요 없다. 릴리스 문서의 기대값은
  "14 packages at v0.7.0", 산출물 28개로 바꾼다.
- PyPI 첫 게시: 계정 소유자가 **Publishing → Pending publishers**에 `de-censor`를 등록한다(0.6의 `de-aquila` 절차와 같다).
- `docs/release.md`에 v0.7.0 절차, `docs/upgrade-0.7.md`(기존 사용자는 바뀌는 것 없음, 핀만 변경), CHANGELOG `## [0.7.0]`.
- 플러그인 `plugins/de-arsenal/.claude-plugin/plugin.json` 버전을 올리고 `evaluate` 스킬을 추가한다.

## 11. 구현 순서 (계획 문서에서 세분화)

1. `censor` 패키지 골격, 체크·결과 모델, 채점·등급, JSON 스키마
2. Quick 체크 5개 차원 + 성숙도 픽스처
3. 이력·리포트·`report`/`compare`
4. Standard: 신뢰성·신선도, 데이터 상태(파일·DuckDB), 규모 보호
5. Postgres 대상 (`TABLESAMPLE` 확인 항목 먼저)
6. Augur 연계 (`de-censor[augur]`)
7. `arsenal eval` 별칭, `de-arsenal[eval]`
8. `evaluate` 스킬과 `attach-findings`
9. 0.7.0 릴리스 준비
