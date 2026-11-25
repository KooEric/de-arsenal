# Censor Plan 2: Standard 단계 (개요 계획)

> **새 세션에서 시작하는 사람에게**: 이 문서는 작업 목록과 결정 사항만 담은 개요다. Plan 1처럼 코드까지
> 적은 상세 계획이 아니다. 각 작업을 시작하기 전에 "읽을 것"의 파일을 먼저 읽고, 작업마다 테스트를 먼저
> 쓴다(TDD). 작업 0은 나머지 작업의 전제를 확인하는 조사이므로 반드시 먼저 한다.

- 상태: 계획만 있음, 구현 전 (2026-11-26)
- 선행: Plan 1 완료, **v0.7.0 배포됨** (14개 패키지, `de-censor` 포함). `main` = `c3ee7e2`.
- 설계 스펙: [2026-11-25-censor-design.md](2026-11-25-censor-design.md) §3(차원), §6(규모 보호), §8(오류), §9(테스트)
- Plan 1 상세 계획(코드 스타일·테스트 방식의 본보기): [2026-11-25-censor-plan1-foundation.md](2026-11-25-censor-plan1-foundation.md)

## 목표

`censor audit --tier standard`가 실행 상태와 실제 데이터를 **읽기만** 해서 세 차원(신뢰성, 신선도, 데이터
상태)을 추가로 채점한다. 프로젝트 없이 파일·DuckDB 테이블·Postgres 테이블만 줘도 평가할 수 있다. 큰
데이터는 읽기 전에 규모를 재고, 기준을 넘으면 건너뛰면서 선택지를 안내한다.

## 지금 코드 상태 (Plan 1이 남긴 것)

| 파일 (`packages/censor/src/censor/`) | 내용 |
|---|---|
| `model.py` | `Status`, `Dimension`(8개 모두 정의됨), `DIMENSION_WEIGHTS`, `CheckResult`, `AuditReport`. `tier`는 이미 `"quick" \| "standard"`, `target_kind`는 `"project" \| "files" \| "duckdb" \| "postgres"`를 허용한다 |
| `context.py` | `ProjectContext`, `SpecFile`(`read_error` 포함), `load_context(root)`. `arsenal.yaml`이 없으면 `FatalError` |
| `runner.py` | `CheckGroup(name, dimension, fn)`, `run_checks(ctx, groups)`. 체크 함수 타입이 `ProjectContext`에 묶여 있다 |
| `checks/` | Quick 5종: `correctness`, `guards`, `security`, `operability`, `agent`. `QUICK_CHECKS`는 `checks/__init__.py` |
| `audit.py` | `audit_project(root)`. tier는 `"quick"`, target_kind는 `"project"`로 고정 |
| `history.py` | `save_report`(원자적 쓰기, 심볼릭 링크 거부), `list_reports` → `HistoryListing`, `unique_run_id` |
| `cli.py` | `audit --tier quick\|standard`(`standard`는 지금 종료 코드 2), `report`, `compare`, `run_cli` |

재사용할 기존 함수(새로 만들지 말 것):

- `arsenal.stages.pipeline_status(path)` → `units`(상태별 개수), `rejected_rows`, `last_completed_at`, `alert`
- `arsenal.dataset.status_report(path)` → `.arsenal/dataset.duckdb`의 게시 상태와 `max_age_hours` 판정(읽기 전용으로 연다)
- `pugio.dlq.dlq_dir`, `split_rejected_rows`
- Postgres 읽기: `pugio/sources/database.py`의 DuckDB `ATTACH ... (TYPE POSTGRES)` 방식과 오류 분류

## 지켜야 할 제약 (Plan 1과 동일, 바뀌지 않음)

- 평가 대상에 아무것도 쓰지 않는다. 쓰는 곳은 `<base>/.arsenal/censor/`뿐이다. DuckDB 파일은 `read_only=True`,
  Postgres는 `READ_ONLY`로 ATTACH한다.
- 파이프라인을 다시 실행하지 않는다.
- 등급은 결정적이다. 같은 입력이면 같은 등급.
- stdout에는 JSON만, 사람용 문구는 stderr. 종료 코드 0 / 1 / 2.
- DSN은 환경변수 이름으로만 받는다(`--postgres-dsn-env`). 오류 메시지에 DSN을 넣지 않는다.
- 비밀값은 출력하지 않는다.
- pyright strict, ruff(100열), 커버리지 80% 게이트. Postgres 테스트는 testcontainers + `importorskip`.
- 차원 가중치: 신뢰성 15, 신선도 10, 데이터 상태 15 (`DIMENSION_WEIGHTS`에 이미 있음).
- 리포트 스키마를 바꾸면 `uv run python scripts/gen_schema_docs.py`로 `schemas/censor-report.json`을 다시 만든다.
  `schema_version`은 1을 유지하고 필드는 추가만 한다.

## 작업

### 작업 0: 전제 확인 (조사, 코드 없음)

결과를 이 문서 맨 아래 "조사 결과"에 적고 나서 다음 작업으로 간다.

1. **상태 읽기가 쓰기를 일으키는가.** `pipeline_status`는 `StateStore(db_path)`를 연다. StateStore가 열 때
   마이그레이션이나 WAL 파일을 만들면 "읽기만 한다"는 제약이 깨진다. `arsenal-core`의 StateStore를 읽고,
   실제로 연 뒤 state DB의 해시와 디렉터리 목록이 그대로인지 확인한다. 쓰기가 생기면 SQLite를
   `mode=ro`로 직접 여는 읽기 전용 경로를 작업 2에서 만든다.
2. **Postgres 표본.** DuckDB postgres 확장의 `postgres_query()`로 `TABLESAMPLE SYSTEM (p)`를 서버에서 실행할
   수 있는지, `READ_ONLY` ATTACH에서도 되는지 testcontainers로 확인한다. 안 되면 Postgres는 `--since`만
   지원하고 `--sample`은 종료 코드 2로 거부한다(스펙 §6에 정해 둔 대안).
3. **Postgres 규모 추정.** `pg_class.reltuples`와 `pg_total_relation_size`를 같은 연결로 읽을 수 있는지 확인한다.

### 작업 1: 대상 일반화

지금은 모든 체크가 `ProjectContext`를 받는다. 데이터만 주는 대상을 받으려면 구조를 먼저 넓혀야 한다.

- 대상 모델을 만든다: 프로젝트, 파일(`--files` glob, `--key`, `--time-column`), DuckDB(`--duckdb`, `--table`),
  Postgres(`--table`, `--postgres-dsn-env`). 공통 옵션은 `--key`, `--time-column`.
- `CheckGroup`에 "어떤 대상 종류에 적용되는지"를 선언하게 하고, 적용되지 않는 그룹은 러너가 실행하지 않는다.
  그러면 스펙 §3의 "해당 없는 차원은 na"가 러너 한 곳에서 나온다.
- `audit.py`가 tier와 `target_kind`를 실제 값으로 채운다.
- 같이 정리: 체크 모듈 4개에 복사돼 있는 결과 생성 헬퍼(`_r`/`_result`)를 하나로 합친다. 새 체크 모듈을
  추가하기 전에 해야 복사본이 더 늘지 않는다.
- 완료 기준: Quick 동작과 기존 테스트 전부가 그대로 통과한다. 성숙도 점수(70.21 / 87.29 / 96.46 / 100)가
  바뀌지 않는다.

### 작업 2: 신뢰성·신선도 (프로젝트 대상)

- 신뢰성: 실패 unit 수, DLQ 비율(`rejected_rows`), 실패한 뒤에도 마지막 게시본이 남아 있는지.
- 신선도: 마지막 성공 시각, `max_age_hours` 설정 여부와 초과 여부, `arsenal dataset` 게시 상태.
- 한 번도 실행하지 않은 파이프라인(`never-run`)은 fail이 아니라 na다. 측정할 것이 없기 때문이다.
- 다른 실행이 lease를 쥐고 있으면 기다리지 않고 warn을 남긴다(스펙 §8).
- 시각 비교는 `now`를 주입받아 테스트가 결정적이게 한다(Plan 1의 `audit_project(now=...)`와 같은 방식).

### 작업 3: 데이터 상태 (파일, DuckDB)

- 측정: 행 수, 키 유일성(`--key`), 컬럼별 NULL 비율, 데이터 기간(`--time-column`의 min/max).
- 프로젝트 대상에서는 스펙에서 대상을 유도한다: sink 경로와 `merge_key`, transform의 `output`. 유도할 수 없으면 na.
- 프로파일링 SQL은 DuckDB 하나로 쓴다(파일은 `read_parquet`/`read_csv`, DuckDB 파일은 읽기 전용 연결).
- NULL 비율의 warn/fail 기준값은 이름 있는 상수로 둔다. 값은 구현하면서 정하고 이 문서에 적는다.

### 작업 4: 규모 보호 (스펙 §6 그대로)

- 읽기 전에 규모를 잰다: 파일 수와 `stat` 크기 합, DuckDB `estimated_size`.
- 기준 상수(`censor/limits.py`): `MAX_FILES = 2000`, `MAX_BYTES = 5 GB`, `MAX_ESTIMATED_ROWS = 50_000_000`.
- 넘으면 데이터 상태 체크를 na로 두고 스펙 §6의 안내문을 stderr와 리포트에 낸다. 다른 차원은 그대로 평가한다.
- 옵션: `--sample <비율>`, `--since <날짜>`(`--time-column` 필요), `--full`, `--memory-limit`(기본 4GB),
  `--timeout`(기본 120초). DuckDB `temp_directory`는 `.arsenal/censor/tmp/`.
- 시간 초과는 fail이 아니라 na와 사유.
- 표본 결과 표기: 표본에서 중복 발견 → fail, 표본에서 중복 없음 → warn("표본 기준 통과"). 리포트 헤더에
  표본 비율을 적는다.

### 작업 5: Postgres 대상

- 작업 0의 결과에 따라 `--sample` 지원 여부를 정한다.
- `ATTACH ... (TYPE POSTGRES, READ_ONLY)`, `statement_timeout`을 `--timeout`과 같은 값으로.
- 접속·인증 실패, 테이블 없음 → 데이터 차원 na, 종료 코드 2, 메시지에 DSN 없음.
- testcontainers 테스트: 중복 키가 있는 테이블 → fail, 추정 행 수로 규모 기준, 시간 초과, 평가 전후 행 수 동일.

### 작업 6: CLI, 리포트, 문서

- `--tier standard`를 실제로 동작시키고 기본값을 `standard`로 바꾼다(스펙 §2). **행동 변화다**: 0.7.0의 기본은
  `quick`이다. CHANGELOG와 업그레이드 문서에 적는다. 걱정되면 기본값은 `quick`으로 두고 다음 minor에서
  바꾸는 안을 사용자에게 먼저 물어본다.
- Markdown 리포트에 표본·규모 안내 구역을 추가한다. Parquet 이력에는 이미 `tier`, `target`, `target_kind`가 있다.
- `arsenal eval`은 인자를 그대로 넘기므로 따로 고칠 것이 없다. 다만 `arsenal eval --help`가 넘겨지는 옵션을
  보여 주지 않는 문제를 이때 고친다(`context_settings`에 `"help_option_names": []`).
- 문서: `packages/censor/README.md`, `docs/tools.md` 10절, `README.md`·`README.ko.md`의 Censor 행, CHANGELOG.

### 작업 7: 읽기 전용 보장 테스트와 전체 검사

- 평가 전후로 대상 파일 해시, DuckDB 파일 해시, state DB 해시, Postgres 행 수가 같은지 확인하는 테스트.
- e2e: `arsenal init csv-cleanup` → `arsenal run` → `censor audit . --tier standard`가 세 차원에 점수를 낸다.
- 전체 검사: `uv run ruff format --check . && uv run ruff check . && uv run pyright && uv run python scripts/gen_schema_docs.py --check && uv run pytest`. Docker를 띄우고 돌려야 컨테이너 테스트 36개가 실행된다.

## Plan 1에서 넘어온 사소한 지적 (작업하면서 처리)

- 저장이 Markdown·JSON 단계에서 실패하면 `runs/<id>.parquet`만 남는다. 실패 시 지우거나, JSON이 없는
  Parquet은 집계에서 빼라고 문서에 적는다.
- 없는 폴더에서 `censor report`가 종료 코드 0을 낸다. 폴더가 없으면 2를 내는 편이 계약에 맞다.
- 읽을 수 없는 `arsenal.yaml`이 종료 코드 1(매니페스트 fail)이다. 계약은 "읽을 수 없는 대상 = 2".
- `_DAG_TRIGGER`가 `schedule` 부분 문자열이라 주석도 통과시킨다. `agent.secret_hook`은 훅이
  `settings.json`에 등록됐는지 보지 않는다.
- `correctness`가 환경변수 미설정을 오류 메시지의 앞부분 문자열로 판별한다. `arsenal_core`에 전용 예외를
  두는 편이 안전하다.
- 임시 파일 이름이 고정이라 같은 프로젝트를 동시에 감사하면 겹친다.

## Plan 2에 넣지 않는 것

- Augur 연계(`--augur-cases`), `evaluate` 스킬, `attach-findings`, Full 단계 서브에이전트 리뷰 → Plan 3.
- Redshift·Hive 테이블 프로파일링, dbt·Airflow 프로젝트 평가 → 스펙에서 범위 밖.
- 골든 케이스 자동 생성.

## 릴리스

- 0.7.x 패치로 낸다. 리포트 스키마는 필드 추가만 하므로 0.7.0 소비자가 깨지지 않는다.
- 기본 tier를 바꾸면(작업 6) 행동 변화이므로 CHANGELOG의 맨 위에 적는다.
- 절차는 `docs/release.md`의 0.6.x 패치 절차와 같다. 새 PyPI 프로젝트가 없으므로 pending publisher 단계는 없다.

## 실행 방식

- 브랜치: `feat/censor-standard` (main에서 분기).
- Plan 1은 작업을 묶음으로 나눠 서브에이전트에게 맡기고 묶음마다 리뷰했다. 같은 방식을 권한다:
  작업 0~1, 작업 2~3, 작업 4~5, 작업 6~7.
- 저장소에는 `git commit` 줄에 `-n` 플래그가 있으면 막는 훅이 있다. `grep -n` 같은 명령과 한 줄에 묶지 않는다.
- uv는 같은 버전의 wheel을 캐시한다. 빌드한 wheel을 다시 설치해 확인할 때는 `--no-cache --reinstall`을 쓴다.

## 조사 결과 (작업 0에서 채운다)

- StateStore 열기의 쓰기 여부:
- Postgres `TABLESAMPLE` pushdown:
- Postgres 규모 추정 쿼리:
