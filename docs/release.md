# v0.6.12 패치 릴리스 절차

`arsenal status`가 `pipelines:`의 수집 파이프라인을 보고하지 않아 수집이 실패해도 `alert: false`에
exit 0을 내던 문제와, 새 프로젝트에서 두 실행이 동시에 시작하면 상태 DB를 만들다 `database is locked`로
죽던 문제의 수정이다(#43). 내역은 [CHANGELOG](../CHANGELOG.md).

**patch인 이유**: 명령, 옵션, 저장 형식, 상태 파일이 바뀌지 않는다. 마이그레이션은 기존 것을 쓰기 락
안에서 스키마 버전을 다시 읽도록 고쳤을 뿐 새로 추가하지 않았다. 교차 핀 `>=0.6,<0.7`을 유지하고, 신규
PyPI 프로젝트가 없다.

**사용자 고지 (CHANGELOG Upgrade notes와 같음):** 수집 파이프라인에 `failed`·`quarantined` unit이 있으면
`arsenal status`가 exit 1을 낸다. 출력만 보려고 `set -e`나 CI 단계 안에서 돌리던 스크립트는 거기서 멈춘다.

1. 이 PR CI green → `main` 머지
2. `main`에서 검증: 아래 0.6.0 절차의 2번과 같다. 기대값은 "13 packages at v0.6.12", 산출물 26개.
3. 수동 점검
   - [ ] PyPI에서 설치한 0.6.12로, 새 디렉터리에서 `arsenal init csv-cleanup` 뒤 `arsenal status`가
         `pipelines`에 `never-run`을 보고하고 exit 0이다. 이어서
         `printf 'id,customer,zip,amount\n1,"Alice\n' > input/bad.csv` → `arsenal run`(CSV 파싱 오류)
         → `arsenal status`가 `alert: true`, `units: {"failed": 1}`, exit 1이다. (PR에서 소스 체크아웃으로
         같은 순서를 돌려 이 결과를 확인했다.)
   - [ ] CHANGELOG `## [0.6.12]`의 날짜 (PR에서 2026-11-24로 기입. 태그가 다른 날이면 고친다).
4. `git tag v0.6.12 && git push origin v0.6.12` (계정 소유자). 신규 프로젝트가 없으므로 수동 최초 게시는 필요 없다.
5. 게시 확인: 13개 패키지의 `https://pypi.org/pypi/<name>/0.6.12/json`이 모두 `200`. 게시 직후 인덱스가
   늦으면 `pugio`가 이전 버전으로 잡힐 수 있으니 버전을 함께 본다.

---

# v0.6.11 패치 릴리스 절차

새로 설치하면 DuckDB 2.0이 잡히는데, 0.6.10은 lock의 1.5.4로만 테스트돼 Postgres 게시마다
`UserWarning`을 냈다. 그 수정과, 허용 범위의 최신 의존성으로 테스트하는 CI(`latest-deps`)다.
내역은 [CHANGELOG](../CHANGELOG.md).

**patch인 이유**: 동작은 같고 경고와 불필요한 복사가 사라진다. 옵션, 저장 형식, 상태 파일이 바뀌지
않는다. 교차 핀 `>=0.6,<0.7`을 유지하고, 상태 마이그레이션과 신규 PyPI 프로젝트가 없다.

1. 이 PR CI green(`latest-deps` 포함) → `main` 머지
2. `main`에서 검증: 아래 0.6.0 절차의 2번과 같다. 기대값은 "13 packages at v0.6.11", 산출물 26개.
3. CHANGELOG `## [0.6.11]`의 날짜 (PR에서 2026-11-23으로 기입. 태그가 다른 날이면 고친다).
4. `git tag v0.6.11 && git push origin v0.6.11` (계정 소유자). 신규 프로젝트가 없으므로 수동 최초 게시는 필요 없다.
5. 게시 확인: 13개 패키지의 `https://pypi.org/pypi/<name>/0.6.11/json`이 모두 `200`. 깨끗한 환경에
   `de-arsenal[postgres,excel]==0.6.11`을 설치해 DuckDB가 2.x로 잡힌 상태에서 Postgres 게시가 경고
   없이 끝나는지 본다. 게시 직후 인덱스가 늦으면 `pugio`가 이전 버전으로 잡힐 수 있으니 버전을 함께 본다.

---

# v0.6.10 패치 릴리스 절차

`arsenal dataset apply`가 검수한 스냅샷을 Postgres 테이블에도 게시하는 옵션
(`--postgres-table schema.table`, `--postgres-dsn-env`)이다. 내역은 [CHANGELOG](../CHANGELOG.md).

**patch인 이유**: 옵션 두 개를 더할 뿐이고, 지정하지 않으면 동작이 0.6.9와 같다. 저장 형식
(`.arsenal/dataset.duckdb`)과 상태 파일은 바뀌지 않는다. 의존성은 이미 있는 `postgres` extra를
쓴다. 교차 핀 `>=0.6,<0.7`을 유지하고, 상태 마이그레이션과 신규 PyPI 프로젝트가 없다.
`pugio.sinks.postgres._pg_type`이 공개 이름 `pg_column_type`이 됐다. 밑줄로 시작하는 내부
이름이라 호환 약속 대상이 아니다.

1. 이 PR CI green → `main` 머지
2. `main`에서 검증: 아래 0.6.0 절차의 2번과 같다. 기대값은 "13 packages at v0.6.10", 산출물 26개.
   Docker가 켜진 머신에서 돌려 Postgres 테스트가 skip되지 않게 한다(`10 skipped` 안팎).
3. 수동 점검
   - [ ] 회사 Postgres의 테스트 스키마에 `arsenal dataset apply --postgres-table <schema>.<table>
         --postgres-dsn-env <VAR>`로 실제 대장을 한 번 게시하고, 다른 계정에서 조회되는지 본다.
         같은 테이블에 뷰를 하나 만든 뒤 다시 `apply`해서 뷰가 남는지 본다.
   - [ ] CHANGELOG `## [0.6.10]`의 날짜 (PR에서 2026-11-23으로 기입. 태그가 다른 날이면 고친다).
4. `git tag v0.6.10 && git push origin v0.6.10` (계정 소유자). 신규 프로젝트가 없으므로 수동 최초 게시는 필요 없다.
5. 게시 확인: 13개 패키지의 `https://pypi.org/pypi/<name>/0.6.10/json`이 모두 `200`. 게시 직후에는
   인덱스가 늦게 갱신돼 `pugio`가 이전 버전으로 설치될 수 있으니 버전을 함께 확인한다.

---

# v0.6.9 패치 릴리스 절차

`arsenal dataset`의 Excel 입력(`.xlsx`·`.xlsm`·`.xlsb`·`.xls`), 파일 소스의 `.xlsb`·`.xlsm`
자동 판별, postgres/mysql `DatabaseSource`의 인증 실패를 재시도하지 않고 바로 멈추게 한 수정이다.
내역은 [CHANGELOG](../CHANGELOG.md).

**patch인 이유**: 거부되던 입력 형식을 받아들이고, 재시도하던 영구 오류를 바로 실패시킨다.
`de-arsenal`에 `excel` extra가 새로 생기지만 기존 설치와 명령, 옵션, 저장 형식
(`.arsenal/dataset.duckdb`)은 바뀌지 않는다. 교차 핀 `>=0.6,<0.7`을 유지하고, 상태
마이그레이션과 신규 PyPI 프로젝트가 없다.

**동작 변화:** 잘못된 비밀번호·없는 DB로 실패하던 scanner 소스는 이제 재시도
없이 바로 failed가 된다. 재시도 횟수에 기대던 모니터링이 있다면 더 빨리 실패를 보게 된다.

1. 이 PR CI green → `main` 머지
2. `main`에서 검증: 아래 0.6.0 절차의 2번과 같다. 기대값은 "13 packages at v0.6.9", 산출물 26개.
3. 수동 점검
   - [ ] `uv tool install "de-arsenal[excel]==0.6.9"` 뒤 실제 `.xlsb` 대장 폴더에서
         `arsenal dataset preview --files '*.xlsb' --key <사번 열>`이 행 수와 앞자리 0을 그대로
         보여 준다. 이 저장소에는 `.xlsb` 샘플이 없어 실제 파일로 읽어 본 적이 없다.
   - [ ] 틀린 비밀번호로 postgres(또는 mysql) `DatabaseSource`를 한 번 실행해 재시도 없이 바로
         failed가 되는지 본다. 메시지 문구로 판별하므로 서버 `lc_messages`가 영어가 아니면 다시
         재시도로 분류될 수 있다.
   - [ ] CHANGELOG `## [0.6.9]`의 날짜 (PR에서 2026-11-23으로 기입. 태그가 다른 날이면 고친다).
4. `git tag v0.6.9 && git push origin v0.6.9` (계정 소유자). 신규 프로젝트가 없으므로 수동 최초 게시는 필요 없다.
5. 게시 확인: 13개 패키지의 `https://pypi.org/pypi/<name>/0.6.9/json`이 모두 `200`.

---

# v0.6.8 패치 릴리스 절차

`arsenal dataset`이 전부 NULL인 컬럼이 있는 JSONL 스냅샷을 스키마 변경으로 거부하던 문제의
수정과, Cloudflare D1 테이블을 스냅샷으로 내보내는 예제다. 내역은 [CHANGELOG](../CHANGELOG.md).

**patch인 이유**: 거부되던 입력 하나를 받아들이게 하는 수정이다. 명령, 옵션, 저장 형식
(`.arsenal/dataset.duckdb`)은 바뀌지 않는다. 교차 핀 `>=0.6,<0.7`을 유지하고, 상태
마이그레이션과 신규 PyPI 프로젝트가 없다.

1. 이 PR CI green → `main` 머지
2. `main`에서 검증: 아래 0.6.0 절차의 2번과 같다. 기대값은 "13 packages at v0.6.8", 산출물 26개.
3. CHANGELOG `## [0.6.8]`의 날짜 (PR에서 2026-11-22로 기입. 태그가 다른 날이면 고친다).
4. `git tag v0.6.8 && git push origin v0.6.8` (계정 소유자). 신규 프로젝트가 없으므로 수동 최초 게시는 필요 없다.
5. 게시 확인: 13개 패키지의 `https://pypi.org/pypi/<name>/0.6.8/json`이 모두 `200`.
6. 출시 후 권장: PyPI에서 설치한 0.6.8로 `examples/real-world/d1-snapshot`의 JSONL 경로를 한 번
   돌리고, 한 컬럼을 전부 NULL로 만든 스냅샷이 `updated`로 보고되는지 확인한다.

---

# v0.6.6 패치 릴리스 절차

손으로 고친 스펙을 실행 전에 검증하는 `arsenal check`, stale일 때 exit 1을 내도록 고친
`pugio status --max-age-seconds`, 그리고 에이전트 스킬 플러그인(`plugins/de-arsenal`)이다.
내역은 [CHANGELOG](../CHANGELOG.md).

**patch인 이유**: 새 명령 하나를 추가하고, 문서(`examples/deploy/README.md`)가 이미 exit 1로
적고 있던 동작을 문서대로 고친다. YAML 필드와 상태 파일은 바뀌지 않는다. 교차 핀
`>=0.6,<0.7`을 유지하고, 상태 마이그레이션과 신규 PyPI 프로젝트가 없다.

**사용자 고지 (CHANGELOG Upgrade notes와 같음):** `pugio status --max-age-seconds`를 출력만
보려고 돌리던 스크립트가 `set -e`나 CI 단계 안에 있으면 stale일 때 거기서 멈춘다.

1. 이 PR CI green → `main` 머지
2. `main`에서 검증: 아래 0.6.0 절차의 2번과 같다. 기대값은 "13 packages at v0.6.6", 산출물 26개.
3. 수동 점검
   - [ ] 새 디렉터리에서 `arsenal init csv-cleanup` → `arsenal check`가 `all specs valid`를
         내고, `collect.yaml`의 `type: parquet`를 오타로 바꾸면 exit 1이 된다.
   - [ ] Claude Code에서 `/plugin marketplace add KooEric/de-arsenal` →
         `/plugin install de-arsenal@de-arsenal`이 되고 스킬 7개가 보인다. 매니페스트는 JSON
         문법만 확인했고 실제 설치는 해 본 적이 없다.
   - [ ] CHANGELOG `## [0.6.6]`의 날짜 (PR에서 2026-11-22로 기입. 태그가 다른 날이면 고친다).
4. `git tag v0.6.6 && git push origin v0.6.6` (계정 소유자). 신규 프로젝트가 없으므로 수동 최초 게시는 필요 없다.
5. 게시 확인: 13개 패키지의 `https://pypi.org/pypi/<name>/0.6.6/json`이 모두 `200`.
6. 출시 후 권장: 스킬을 실제 REST API와 DB 소스, cron에 한 번씩 써 본다. 스킬의 그 부분은
   스키마 문서와 `examples/deploy`를 보고 썼고 실행된 적이 없다.

---

# v0.6.5 패치 릴리스 절차

Trino에서 읽기 위한 두 가지 추가다: Iceberg sink(`pugio[iceberg]`)와 `pugio ddl`.
내역은 [CHANGELOG](../CHANGELOG.md).

**patch인 이유**: 새 sink 타입과 새 명령만 추가하고 기존 동작은 바꾸지 않는다. 교차 핀
`>=0.6,<0.7`을 유지하고, 상태 마이그레이션과 신규 PyPI 프로젝트가 없다.

**순서 주의**: `v0.6.0` 태그가 `97f57e7`에 올라가 13개 패키지가 게시된 뒤에 이 PR을 머지한다.
먼저 머지하면 `main`의 버전이 0.6.5라 `v0.6.0` 태그 실행이 메타데이터 검사에서 실패한다
(그 경우 `git tag v0.6.0 97f57e7`처럼 커밋을 지정해 태그한다).

1. 이 PR CI green → `main` 머지
2. `main`에서 검증: 아래 0.6.0 절차의 2번과 같다. 기대값은 "13 packages at v0.6.5", 산출물 26개.
3. CHANGELOG `## [0.6.5]`의 날짜를 확인한다 (PR에서 2026-11-18로 기입).
4. `git tag v0.6.5 && git push origin v0.6.5` (계정 소유자). 신규 프로젝트가 없으므로 수동 최초 게시는 필요 없다.
5. 게시 확인: 13개 패키지의 `https://pypi.org/pypi/<name>/0.6.5/json`이 모두 `200`.
6. 출시 후 권장: 실제 카탈로그(REST/Glue)와 S3에 Iceberg sink로 한 번 쓰고 Trino에서 조회한다.
   `pugio ddl`이 출력한 문장을 Trino에서 한 번 실행한다. 둘 다 그 환경에서 검증된 적이 없다.

---

# v0.6.0 릴리스 절차

13번째 패키지 **Pilum**(`de-pilum`, experimental)이 처음 배포된다. 내역은
[CHANGELOG](../CHANGELOG.md), 사용자 절차는 [upgrade-0.6.md](upgrade-0.6.md).

**minor인 이유**: 새 배포 패키지가 들어가고, 파일 소스의 동작 세 가지가 호환되지 않게 바뀐다
(매칭 0건은 에러, 앞자리 0 컬럼은 문자열, 파일 간 타입 불일치는 경고). 교차 핀을
`>=0.6,<0.7`로 옮긴다. 상태 마이그레이션은 없다(StateStore v3, `aquila.db` v2 그대로).

## 확정 정책

- 배포 패키지 13개를 v0.6.0으로 동기화한다: `arsenal-core`, `de-arsenal`, `pugio`,
  `de-gladius`, `de-scorpio`, `de-scutum`, `de-onager`, `spatha`, `de-augur`, `de-hasta`,
  `de-ballista`, `de-aquila`, **`de-pilum`** (굵은 것은 PyPI 첫 게시).
- `de-arsenal` 기본 설치는 Pilum을 끌어오지 않는다. `de-arsenal[dispatch]` extra다.
- `de-pilum`은 워크스페이스 패키지 중 `arsenal-core`에만 의존한다.
- 상태 표기: Arsenal·Pugio·Gladius **stable**, Onager·Scutum·Scorpio·Spatha·Hasta·Ballista
  **beta**, Aquila **preview**, Augur·Pilum **experimental**.

## 절차

1. 이 PR CI green → `main` 머지
2. `main`에서 검증

   ```bash
   uv sync
   uv run python scripts/check_release_metadata.py   # "13 packages at v0.6.0"
   uv run ruff check . && uv run ruff format --check .
   uv run pyright
   uv run python scripts/gen_schema_docs.py --check
   uv run pytest
   uv build --all-packages && ls dist/ | wc -l       # sdist + wheel × 13 = 26
   ```

3. 수동 점검
   - [ ] 새 디렉터리에서 README 첫 블록 그대로: `arsenal init csv-cleanup` → `arsenal run` →
         `arsenal query ...`가 3행을 보여 준다.
   - [ ] `de_arsenal` wheel에 `arsenal/recipes/csv-cleanup/input/sample.csv`가 들어 있다
         (`unzip -l dist/de_arsenal-0.6.0-*.whl | grep sample.csv`).
   - [ ] `examples/pilum`의 주석대로 실행해 두 번째 `pilum run`이 0건을 보낸다.
   - [ ] Redshift sink를 테스트용 테이블에 `load: insert`와 `load: copy`로 한 번씩 돌린다. 실제
         클러스터에서 검증된 적이 없다. 안 되면 CHANGELOG에 그대로 적고 experimental로 둔다.
   - [ ] `examples/deploy`의 워크플로와 Dockerfile을 한 번씩 실행·빌드한다.
   - [x] CHANGELOG `## [0.6.0]`의 날짜 (PR에서 2026-11-18로 기입. 태그가 다른 날이면 고친다).
4. PyPI 준비 — 신규 배포명 1개 (계정 소유자). PyPI **Publishing → Pending publishers**에 등록한다.

   | 필드 | 값 |
   |---|---|
   | PyPI project name | `de-pilum` |
   | Owner / Repository | `KooEric` / `de-arsenal` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

5. 검증 전용 실행 → `de-pilum` 최초 게시 → 태그

   ```bash
   gh workflow run release.yml -f version=0.6.0 -f publish_package=none
   gh workflow run release.yml -f version=0.6.0 -f publish_package=de-pilum
   git tag v0.6.0 && git push origin v0.6.0
   ```

   태그 실행이 나머지 12개를 게시하고, 먼저 게시된 `de-pilum`은 `skip-existing`으로 건너뛴다.

6. 게시 확인 — 13개 모두 `200`, 새 venv 설치

   ```bash
   for p in arsenal-core de-arsenal pugio de-gladius de-scorpio de-scutum de-onager spatha \
            de-augur de-hasta de-ballista de-aquila de-pilum; do
     curl -s -o /dev/null -w "$p %{http_code}\n" "https://pypi.org/pypi/$p/0.6.0/json"
   done
   uv tool install --force "de-arsenal[streaming,distributed,catalog,dispatch]==0.6.0"
   arsenal --help && pilum --help
   ```

7. GitHub Release `v0.6.0`

   ```bash
   gh release create v0.6.0 --title v0.6.0 --notes-file <(sed -n '/^## \[0.6.0\]/,/^## \[0.5.3\]/p' CHANGELOG.md | sed '$d')
   ```

**0.5.3 릴리스에서 확인된 것:**

- Claude 세션에서는 태그 push와 `release.yml` 수동 실행이 되지 않는다(403, 또는 원격에 태그가
  생기지 않음). 4~5번은 계정 소유자가 직접 한다. `gh` 대신 GitHub 웹의 Releases → "Draft a new
  release"에서 새 태그 `v0.6.0`으로 Release를 만들어도 `release.yml`이 실행된다. 태그로 시작된
  실행도 `verify-and-build`를 통과해야 게시하므로, 검증 전용 실행을 생략해도 안전장치는 남는다.
- `ballista-e2e`는 Docker 빌드 중 Maven Central 429로 실패할 수 있다. 코드와 무관하며 다시 돌리면 된다.
- PR #23(`claude/peaceful-rubin-2er7lh`: 실데이터 실험 문서, Augur `OPENAI_BASE_URL`)의 커밋은 이
  브랜치에 병합돼 있다. 어느 쪽을 먼저 머지해도 된다.

실패 시: PyPI에 올라간 버전은 덮어쓸 수 없다. 일부만 게시된 상태에서 결함이 나오면 13개 전체를
0.6.1로 다시 릴리스하고 0.6.0은 yank한다.

---

# v0.5.3 패치 릴리스 절차 (참고: 마지막 patch)

실데이터 도그푸딩에서 찾은 조용한 데이터 손실 두 건의 수정이다. 내역은
[CHANGELOG](../CHANGELOG.md#053---2026-11-18).

- **수정:** `github-issues` 레시피·예제·Pugio README의 `mode: offset, param: page`가 첫
  페이지만 수집하던 문제. CSV에서 값이 전부 `NA`인 컬럼이 null 타입으로 추론돼 값을 잃던 문제.
- **추가:** `type: file` 소스의 `null_values` (기본 `[""]`).

**patch인 이유**: 바뀌는 동작 두 가지가 모두 데이터를 잃던 경로다. offset 모드에 페이지
번호형 `param`을 쓰던 스펙은 이미 페이지를 건너뛰고 있었고, `NA`를 null로 읽던 동작은 파일
분할 방식에 따라 결과가 달라졌다. 교차 핀 `>=0.5,<0.6`을 유지하고, 상태 마이그레이션과 신규
PyPI 프로젝트가 없다.

**사용자 고지 (CHANGELOG Upgrade notes와 같음):**
- `mode: offset`에 `page`·`page_number`·`pageNo` 같은 `param`을 쓰면 이제 스펙 검증
  에러다. 메시지대로 `mode: page`로 바꾼다.
- CSV의 `NA`·`N/A`·`null`은 이제 문자열로 읽힌다. 이 토큰을 결측값으로 쓰는 숫자 컬럼은
  `null_values: ["", "NA"]`처럼 지정해야 숫자로 남는다.

## 절차

1. 이 PR CI green → `main` 머지
2. `main`에서 검증

   ```bash
   uv sync
   uv run python scripts/check_release_metadata.py   # "12 packages at v0.5.3"
   uv run ruff check . && uv run ruff format --check .
   uv run pyright
   uv run python scripts/gen_schema_docs.py --check
   uv run pytest
   uv build --all-packages && ls dist/ | wc -l       # 24
   ```

3. CHANGELOG의 `## [0.5.3]`에 날짜가 들어갔는지 확인한다 (머지 전 PR에서 2026-11-18로 기입).
4. 검증 전용 수동 실행 후 태그

   ```bash
   gh workflow run release.yml -f version=0.5.3 -f publish_package=none
   git tag v0.5.3 && git push origin v0.5.3
   ```

5. 게시 확인 — 12개 모두 `200`, 새 venv 설치

   ```bash
   for p in arsenal-core de-arsenal pugio de-gladius de-scorpio de-scutum de-onager spatha \
            de-augur de-hasta de-ballista de-aquila; do
     curl -s -o /dev/null -w "$p %{http_code}\n" "https://pypi.org/pypi/$p/0.5.3/json"
   done
   uv tool install --force "de-arsenal[streaming,distributed,catalog]==0.5.3"
   ```

6. GitHub Release `v0.5.3`

   ```bash
   gh release create v0.5.3 --title v0.5.3 --notes-file <(sed -n '/^## \[0.5.3\]/,/^## \[0.5.2\]/p' CHANGELOG.md | sed '$d')
   ```

실패 시 처리는 아래 0.5.0 절차와 같다(PyPI 재게시 불가 → 0.5.4).

---

# v0.5.0 릴리스 절차 (참고: 마지막 minor)

카탈로그 릴리스다. 12번째 패키지 **Aquila**(`de-aquila`)가 처음 배포된다 — 레포의
스펙·상태 DB(읽기 전용)·Parquet footer를 스캔해 데이터셋·스키마 이력·도구 간 lineage·
계약·태그를 `.arsenal/aquila.db` 하나에 기록하며, 다른 도구는 Aquila에 의존하지 않는다.
전체 내역은 [CHANGELOG](../CHANGELOG.md), 사용자 절차는 [upgrade-0.5.md](upgrade-0.5.md).

**minor인 이유**: 새 배포 패키지와 `arsenal status`의 새 alert 조건(계약 불일치,
`[catalog]` extra 사용 시)이 들어간다. 0.x에서 minor가 호환 축이므로 교차 패키지 핀을
`>=0.5,<0.6`으로 옮긴다. StateStore 스키마는 v3 그대로이고 마이그레이션은 없다.

> 이전 릴리스: v0.4.0은 2026-11-17에 11개 패키지로 게시됐다. 그 절차(StateStore v3,
> 신규 3개 부트스트랩)는 이 문서의 git 이력과 [upgrade-0.4.md](upgrade-0.4.md)에 있다.

## 확정 정책

- 배포 패키지 12개를 v0.5.0으로 동기화한다: `arsenal-core`, `de-arsenal`, `pugio`,
  `de-gladius`, `de-scorpio`, `de-scutum`, `de-onager`, `spatha`, `de-augur`, `de-hasta`,
  `de-ballista`, **`de-aquila`** (굵은 것은 PyPI 첫 게시).
- 교차 패키지 의존 범위는 `>=0.5,<0.6`.
- 루트 workspace 프로젝트는 배포하지 않으므로 `0.0.0`을 유지한다.
- 모든 패키지는 저장소 루트와 같은 Apache-2.0 `LICENSE`를 배포물에 포함한다
  (`check_release_metadata.py`가 확인).
- `de-arsenal` 기본 설치는 Aquila·Hasta·Ballista와 Spark·Kubernetes·Iceberg·Kafka·obstore·
  psycopg를 끌어오지 않는다. Aquila는 `de-arsenal[catalog]` extra.
- `de-aquila`는 `arsenal-core`·`de-scorpio`·`pyarrow`·`pyyaml`·`typer`에만 의존한다.
- 상태 표기: Hasta·Ballista **beta**, Aquila **preview**, Augur **experimental**.

## 머지 순서

1. #19 `feat/p2-phase5-aquila` → `main` (Aquila AQ-0~AQ-4, 0.5.0 버전·문서)
2. `main`에서 아래 검증 후 태그

## 태그 전 검증

```bash
uv sync
uv run python scripts/check_release_metadata.py   # "12 packages at v0.5.0"
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run python scripts/gen_schema_docs.py --check
uv run pytest
uv build --all-packages
ls dist/ | wc -l                                  # sdist + wheel × 12 = 24
```

CI에서 확인할 것: `ci.yml`(3 OS × Python 3.11/3.12, `portable-equivalence`) green.
Ballista 코드는 바뀌지 않았으므로 `ballista-e2e.yml`은 마지막 `main` 실행 결과로 충분하다.

수동 점검:

- [ ] 새 venv에 `pip install de-arsenal`만 했을 때 `de-aquila`가 없다
      (`packages/arsenal/tests/test_base_install.py`가 workspace에서 같은 것을 확인).
- [ ] 실제 프로젝트에서 `aquila scan`을 두 번 돌려 `totals`가 같다.
- [ ] CHANGELOG의 `## [0.5.0] - TBD`를 실제 날짜로 바꾼다.

## PyPI 준비 — 신규 배포명 1개 (계정 소유자)

`de-aquila`만 PyPI 첫 게시 대상이다. PyPI 계정의 **Publishing → Pending publishers**에
등록한다. 같은 저장소·workflow·환경 조합의 pending publisher는 한 번에 하나만 둘 수
있으므로(0.4.0 때 확인), 등록 → 수동 최초 게시 → 성공 확인 순서를 지킨다.

| 필드 | 값 |
|---|---|
| PyPI project name | `de-aquila` |
| Owner / Repository | `KooEric` / `de-arsenal` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

## GitHub 릴리스

검증만 하는 수동 실행:

```bash
gh workflow run release.yml -f version=0.5.0 -f publish_package=none
gh run list --workflow release.yml --limit 1
```

`de-aquila` 최초 게시(pending publisher 등록 후, 검증된 `main` 커밋에서):

```bash
gh workflow run release.yml -f version=0.5.0 -f publish_package=de-aquila
```

성공을 확인한 뒤 태그를 push한다. 태그 실행이 나머지 11개를 게시하고, 먼저 게시된
`de-aquila` 파일은 `skip-existing`으로 건너뛴다.

```bash
git tag v0.5.0
git push origin v0.5.0
```

## 게시 후 확인

PyPI의 `/pypi/<name>/json`은 CDN 캐시로 몇 분간 이전 최신 버전을 보일 수 있다.
버전별 엔드포인트로 확인한다.

```bash
for p in arsenal-core de-arsenal pugio de-gladius de-scorpio de-scutum de-onager spatha \
         de-augur de-hasta de-ballista de-aquila; do
  curl -s -o /dev/null -w "$p %{http_code}\n" "https://pypi.org/pypi/$p/0.5.0/json"
done
uv tool install --force "de-arsenal[streaming,distributed,catalog]==0.5.0"
arsenal --help && aquila --help && hasta --help && ballista --help
```

- 12개 모두 `200`이고, PyPI 프로젝트 페이지에 README가 렌더된다.
- GitHub Release 노트에 CHANGELOG 0.5.0 항목과 upgrade-0.5.md 링크를 붙인다.

## 실패 시 처리

PyPI에 업로드된 버전은 같은 번호로 덮어쓰지 않는다. 일부만 게시된 상태에서 결함이
발견되면 수정 후 **12개 전체를 0.5.1로** 다시 릴리스하고(핀이 `>=0.5,<0.6`이라 자동
선택), 0.5.0은 PyPI에서 yank한다.

## 이름 원칙

배포명만 `de-` 접두어를 쓰고 import·CLI 이름은 짧은 이름을 유지한다(`aquila`, `hasta`,
`ballista`, `gladius` …). import 이름 변경은 별도 마이그레이션 릴리스와 deprecation 기간을 둔다.
