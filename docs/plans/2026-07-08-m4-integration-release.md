# M4: 통합·릴리스 (v0.1.0) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **초안 상태**: M1~M3 완료 후 실제 산출물 기준으로 갱신해 실행한다. 이 마일스톤은 코드보다 검증·문서가 주라 태스크 수준으로 유지한다.

**Goal:** `uv tool install de-arsenal` + `arsenal init <recipe>` + `arsenal run` 세 명령으로 10분 안에 시나리오 A~D가 재현되는 v0.1.0 — "원클릭 경험"의 완성.

---

### Task 4.0: `arsenal` 우산 CLI — 단일 진입점

새 패키지 `packages/arsenal`(배포명 `de-arsenal`, 모듈 `arsenal`, 커맨드 `arsenal`). **새 로직 없음** — pugio·gladius에 위임만 한다. 스켈레톤은 커밋에 이미 있음 — 본문만 채운다.

**Files:**
- Modify: `packages/arsenal/src/arsenal/cli.py`, `packages/arsenal/src/arsenal/project.py`
- Test: `packages/arsenal/tests/test_cli.py`

- [x] **Step 1: 매니페스트 모델 테스트** — `arsenal.yaml`이 프로젝트의 실행 순서를 선언:

```yaml
# arsenal.yaml — arsenal run이 읽는 매니페스트
name: my-project
pipelines:                # pugio로 순서대로 실행
  - collect.yaml
transforms:               # gladius로 순서대로 실행
  - transform.yaml
```

```python
def test_manifest_parses_and_paths_resolve_relative_to_file(tmp_path: Path) -> None:
    (tmp_path / "arsenal.yaml").write_text(MANIFEST)
    proj = load_project(tmp_path / "arsenal.yaml")
    assert proj.pipelines[0] == tmp_path / "collect.yaml"


def test_run_executes_pipelines_then_transforms(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("arsenal.cli._run_pipeline", lambda p: calls.append(f"collect:{p.name}"))
    monkeypatch.setattr("arsenal.cli._run_transform", lambda p: calls.append(f"transform:{p.name}"))
    runner.invoke(app, ["run", "--project", str(tmp_path)])
    assert calls == ["collect:collect.yaml", "transform:transform.yaml"]
```

- [x] **Step 2: 구현** — `project.py`에 `ArsenalProject(_Frozen)` 모델 + `load_project`. `cli.py`:

```python
@app.command()
def init(recipe: str, dest: Path = Path(".")) -> None:
    """레시피로 프로젝트 스캐폴드. arsenal init --list 로 목록."""
    # importlib.resources로 arsenal/recipes/<recipe>/* 를 dest에 복사.
    # 기존 파일 있으면 덮어쓰지 않고 FatalError. 완료 후 다음 명령 안내 출력.

@app.command()
def run(project: Path = Path(".")) -> None:
    """arsenal.yaml 순서대로 수집→변환 일괄 실행. 재실행 = 재개."""
    # pugio.runner.run_pipeline / gladius.engine.run_transform 직접 호출 (서브프로세스 아님)

@app.command()
def query(sql: str, format: str = "table") -> None:
    """gladius.engine.query 위임."""

# collect/transform은 개별 도구 CLI를 add_typer로 마운트:
app.add_typer(pugio_app, name="collect", help="수집 (pugio)")
app.add_typer(gladius_app, name="transform", help="변환 (gladius)")
```

- [x] **Step 3: 커밋** — `git commit -m "feat: arsenal umbrella cli — init/run/query single entrypoint"` (실제 커밋: `3ef3297`)

### Task 4.1: 원클릭 레시피 3종

레시피 = `arsenal/recipes/<name>/` 패키지 데이터: `arsenal.yaml` + 스펙 YAML들 + `README.md`(3줄: 뭘 하는지·뭘 바꿔야 하는지·실행법). **각 레시피는 E2E 테스트로 검증된다 — 깨진 레시피는 원클릭이 아니라 원클릭 사기다.**

**Files:**
- Create: `packages/arsenal/src/arsenal/recipes/{github-issues,csv-cleanup,api-to-postgres}/`
- Test: `tests/e2e/test_recipes.py`

- [x] **Step 1: 레시피 내용** (github-issues는 스켈레톤에 있음 — 검증만):

| 레시피 | 흐름 | 검증 방법 |
|---|---|---|
| `github-issues` | REST(page) → parquet → 정리 변환 → query 안내 | respx 목 E2E |
| `csv-cleanup` | file source 글롭 → parquet → dedup+cast 변환 | tmp csv fixture E2E |
| `api-to-postgres` | REST → validate 게이트 → PG upsert sink | testcontainers E2E |

- [x] **Step 2: E2E** — 각 레시피를 tmp 디렉터리에 `init` → 스펙의 url/경로만 fixture로 치환 → `arsenal run` → 결과 검증. (`tests/e2e/test_recipes.py`)
- [x] **Step 3: 커밋** — `git commit -m "feat: one-click recipes with e2e verification"` (실제 커밋: `5236737`)

### Task 4.2: 연계 예제 — 수집→변환 end-to-end

**Files:**
- Create: `examples/quickstart/` (README.md + collect.yaml + transform.yaml)
- Create: `tests/e2e/test_quickstart.py`

- [x] respx 목이 아닌 **로컬 파일 fixture API**(pytest 내 uvicorn 없이, respx로 충분하면 respx)로 quickstart 예제와 동일한 흐름을 자동 검증: `pugio run collect.yaml` → `gladius run transform.yaml` → 결과 행 검증. (`tests/e2e/test_quickstart.py`)
- [x] quickstart README: 설치(`uv tool install de-arsenal` — 분석가는 venv를 만들지 않는다, pipx 대안 병기)부터 결과 확인까지 복붙 가능한 명령만으로 구성. 중간에 "10분" 초과 요소(도커, 계정 생성 등)가 없어야 한다. (`examples/quickstart/README.md` — offline, no account, no Docker)
- [x] Commit: `docs: quickstart example with verified e2e` (실제 커밋: `78f5853`)

### Task 4.3: YAML 레퍼런스 자동 생성

**Files:**
- Create: `scripts/gen_schema_docs.py`, `docs/reference/pipeline-schema.md`, `docs/reference/transform-schema.md`

- [x] `PipelineSpec.model_json_schema()` / `TransformSpec.model_json_schema()`를 마크다운 표로 렌더 — 필드·타입·기본값·설명이 코드에서 나온다(문서 드리프트 = 버그 원칙의 자동화).
- [x] JSON Schema 파일도 함께 출력(`schemas/*.json`) — 에디터 자동완성용 (`# yaml-language-server: $schema=` 헤더를 예제에 추가).
- [x] CI에 생성물 최신성 체크 추가: `python scripts/gen_schema_docs.py --check`. (본 세션에서 재실행해 exit 0 확인 — 아래 게이트 결과 참고)
- [x] Commit: `docs: generated yaml reference + editor schemas` (실제 커밋: `0eac7a9`)

### Task 4.4: 정직한 한계선 문서

**Files:**
- Create: `docs/08-limits.md`

- [x] 내용 (docs/07 "줄여주지 못하는 비용"과 벤치마크 실측 기반):
  - 단일 노드 처리 한계 — M3 벤치마크 수치로 "이 규모까지는 이 시간" 표
  - 미지원: 실시간 스트리밍(P2 Hasta), 분산(P2 Ballista), 스키마 드리프트 정책(P1)
  - sink별 멱등 보장 범위 표 (parquet: 파일 단위 / duckdb·pg: 키 단위 upsert)
- [x] Commit: `docs: honest limits documentation` (별도 커밋이 아니라 `dc98da8`에 다른 패키징 산출물과 함께 배치됨 — 계획 문서의 커밋 분리 의도와 실제가 다름, 내용은 충족)

### Task 4.5: 패키징·CI 검증

- [x] `uv build --all-packages` 성공, wheel 메타데이터(license, classifiers, urls) 점검 (`docs/reference/packaging.md`)
- [x] 깨끗한 venv에서 wheel 설치 → `arsenal --help`, `pugio --help`, `gladius --help` 동작 확인 (editable 의존 누수 검출 — `psycopg` 지연 import 버그 발견·수정)
- [x] `arsenal-core` 버전 핀: pugio/gladius가 `arsenal-core>=0.1,<0.2`로 의존 (스펙 하위 호환 정책과 일치)
- [x] **CI 매트릭스에 windows-latest 추가** — `.github/workflows/ci.yml`에 반영, YAML 문법 로컬 검증 완료. **실제 GitHub Actions에서 windows-latest job이 초록인지는 push 후에만 확인 가능 — 로컬에서는 검증 불가 (사용자 액션 대기).**
- [x] PyPI 이름 확인: `de-arsenal`·`pugio`·`gladius` 선점 여부 조회 완료 — `de-arsenal`/`pugio`는 미점유, `gladius`는 이미 무관한 기존 패키지가 점유 중. **충돌 해소(접두 전략 채택 여부)는 게시 시점에 저장소 소유자가 결정 — 이 세션에서 이름을 바꾸지 않음.**
- [x] LICENSE(Apache-2.0) 확정, 각 패키지 README 작성
- [x] Commit: `chore: packaging metadata and windows ci for v0.1.0` (실제 커밋 메시지: `chore: packaging metadata, windows ci, license, readmes, and honest limits doc` — `dc98da8`)

### Task 4.6: 릴리스

- [x] CHANGELOG.md 작성 (M1~M4 요약, 스펙 필드 목록) — 본 세션에서 `CHANGELOG.md` 작성 완료
- [x] 전체 게이트 + e2e 1회 — 아래 "전체 게이트 결과" 참고. **`RUN_LIVE=1` 실 GitHub API 스모크는 미실행** — 이 세션에 GitHub 토큰이 없어 의도적으로 생략(기본값이 스킵이므로 스킵 자체는 정상 동작, 다만 "실 API 1회 확인"이라는 의미의 라이브 스모크는 아직 수행되지 않음)
- [ ] `git tag v0.1.0` — **태그 생성은 사용자 승인 대기 (이 작업 지시에서 명시적으로 금지됨).** PyPI 배포는 태그 후 별도 판단(계정 확보·`gladius` 이름 충돌 해소 결정 선행) — **이 역시 사용자 결정 대기, 미착수.**
- [ ] 10분 테스트: 문서만 보고 quickstart를 처음부터 재현 (실패 지점은 곧 문서 버그) — **자동화된 E2E(`tests/e2e/test_quickstart.py`)는 통과하지만, 사람이 문서만 보고 처음부터 수동 재현하는 테스트는 사용자 액션이 필요해 미실행.**

## M4 DoD

- [ ] `uv tool install` → `arsenal init <recipe>` → `arsenal run` 세 명령으로 신규 사용자 10분 재현 (자동화된 quickstart E2E는 통과 — 사람이 문서만 보고 수동으로 재현하는 절차는 사용자 액션 대기, 미실행)
- [x] 레시피 3종 전부 E2E 테스트 통과 (`tests/e2e/test_recipes.py` — github-issues/csv-cleanup/api-to-postgres 전부 그린)
- [x] 생성 문서·스키마가 CI에서 최신성 보장 (`scripts/gen_schema_docs.py --check` — 본 세션에서 재실행해 exit 0 확인)
- [x] wheel 설치만으로 세 CLI(arsenal/pugio/gladius) 동작 (클린 venv에서 `--help` 전부 확인 — `docs/reference/packaging.md`)
- [x] Windows CI 초록 (v0.1.1에서 달성 — run 30275875765, `windows-latest` 3.11/3.12 둘 다 282 passed·21 skipped로 Linux/macOS와 동일. v0.1.0 시점에는 빨간불이었고 원인 2건은 CHANGELOG 0.1.1 참조)
- [x] v0.1.0 태그 (생성·push 완료. 후속 v0.1.1 태그도 동일)
- [ ] PyPI 게시 (`gladius` 이름 충돌 해소 결정 + 사용자의 계정/토큰 필요 — 게시 시점 판단으로 보류, 미착수)
