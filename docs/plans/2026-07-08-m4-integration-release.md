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

- [ ] **Step 1: 매니페스트 모델 테스트** — `arsenal.yaml`이 프로젝트의 실행 순서를 선언:

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

- [ ] **Step 2: 구현** — `project.py`에 `ArsenalProject(_Frozen)` 모델 + `load_project`. `cli.py`:

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

- [ ] **Step 3: 커밋** — `git commit -m "feat: arsenal umbrella cli — init/run/query single entrypoint"`

### Task 4.1: 원클릭 레시피 3종

레시피 = `arsenal/recipes/<name>/` 패키지 데이터: `arsenal.yaml` + 스펙 YAML들 + `README.md`(3줄: 뭘 하는지·뭘 바꿔야 하는지·실행법). **각 레시피는 E2E 테스트로 검증된다 — 깨진 레시피는 원클릭이 아니라 원클릭 사기다.**

**Files:**
- Create: `packages/arsenal/src/arsenal/recipes/{github-issues,csv-cleanup,api-to-postgres}/`
- Test: `tests/e2e/test_recipes.py`

- [ ] **Step 1: 레시피 내용** (github-issues는 스켈레톤에 있음 — 검증만):

| 레시피 | 흐름 | 검증 방법 |
|---|---|---|
| `github-issues` | REST(page) → parquet → 정리 변환 → query 안내 | respx 목 E2E |
| `csv-cleanup` | file source 글롭 → parquet → dedup+cast 변환 | tmp csv fixture E2E |
| `api-to-postgres` | REST → validate 게이트 → PG upsert sink | testcontainers E2E |

- [ ] **Step 2: E2E** — 각 레시피를 tmp 디렉터리에 `init` → 스펙의 url/경로만 fixture로 치환 → `arsenal run` → 결과 검증.
- [ ] **Step 3: 커밋** — `git commit -m "feat: one-click recipes with e2e verification"`

### Task 4.2: 연계 예제 — 수집→변환 end-to-end

**Files:**
- Create: `examples/quickstart/` (README.md + collect.yaml + transform.yaml)
- Create: `tests/e2e/test_quickstart.py`

- [ ] respx 목이 아닌 **로컬 파일 fixture API**(pytest 내 uvicorn 없이, respx로 충분하면 respx)로 quickstart 예제와 동일한 흐름을 자동 검증: `pugio run collect.yaml` → `gladius run transform.yaml` → 결과 행 검증.
- [ ] quickstart README: 설치(`uv tool install de-arsenal` — 분석가는 venv를 만들지 않는다, pipx 대안 병기)부터 결과 확인까지 복붙 가능한 명령만으로 구성. 중간에 "10분" 초과 요소(도커, 계정 생성 등)가 없어야 한다.
- [ ] Commit: `docs: quickstart example with verified e2e`

### Task 4.3: YAML 레퍼런스 자동 생성

**Files:**
- Create: `scripts/gen_schema_docs.py`, `docs/reference/pipeline-schema.md`, `docs/reference/transform-schema.md`

- [ ] `PipelineSpec.model_json_schema()` / `TransformSpec.model_json_schema()`를 마크다운 표로 렌더 — 필드·타입·기본값·설명이 코드에서 나온다(문서 드리프트 = 버그 원칙의 자동화).
- [ ] JSON Schema 파일도 함께 출력(`schemas/*.json`) — 에디터 자동완성용 (`# yaml-language-server: $schema=` 헤더를 예제에 추가).
- [ ] CI에 생성물 최신성 체크 추가: `python scripts/gen_schema_docs.py --check`.
- [ ] Commit: `docs: generated yaml reference + editor schemas`

### Task 4.4: 정직한 한계선 문서

**Files:**
- Create: `docs/08-limits.md`

- [ ] 내용 (docs/07 "줄여주지 못하는 비용"과 벤치마크 실측 기반):
  - 단일 노드 처리 한계 — M3 벤치마크 수치로 "이 규모까지는 이 시간" 표
  - 미지원: 실시간 스트리밍(P2 Hasta), 분산(P2 Ballista), 스키마 드리프트 정책(P1)
  - sink별 멱등 보장 범위 표 (parquet: 파일 단위 / duckdb·pg: 키 단위 upsert)
- [ ] Commit: `docs: honest limits documentation`

### Task 4.5: 패키징·CI 검증

- [ ] `uv build --all-packages` 성공, wheel 메타데이터(license, classifiers, urls) 점검
- [ ] 깨끗한 venv에서 wheel 설치 → `arsenal --help`, `pugio --help`, `gladius --help` 동작 확인 (editable 의존 누수 검출)
- [ ] `arsenal-core` 버전 핀: pugio/gladius가 `arsenal-core>=0.1,<0.2`로 의존 (스펙 하위 호환 정책과 일치)
- [ ] **CI 매트릭스에 windows-latest 추가** — 분석가 상당수가 Windows. `os.replace` 원자성·경로 구분자·인코딩 이슈가 주요 검증 대상
- [ ] PyPI 이름 확인: `de-arsenal`·`pugio`·`gladius` 선점 여부 조회, 충돌 시 접두 전략(`arsenal-pugio` 등) 결정
- [ ] LICENSE(Apache-2.0) 확정, 각 패키지 README 작성
- [ ] Commit: `chore: packaging metadata and windows ci for v0.1.0`

### Task 4.6: 릴리스

- [ ] CHANGELOG.md 작성 (M1~M4 요약, 스펙 필드 목록)
- [ ] 전체 게이트 + e2e + `RUN_LIVE=1` 스모크 1회
- [ ] `git tag v0.1.0` — PyPI 배포는 태그 후 별도 판단(계정·이름 선점 확인 선행)
- [ ] 10분 테스트: 문서만 보고 quickstart를 처음부터 재현 (실패 지점은 곧 문서 버그)

## M4 DoD

- [ ] `uv tool install` → `arsenal init <recipe>` → `arsenal run` 세 명령으로 신규 사용자 10분 재현
- [ ] 레시피 3종 전부 E2E 테스트 통과
- [ ] 생성 문서·스키마가 CI에서 최신성 보장
- [ ] wheel 설치만으로 세 CLI(arsenal/pugio/gladius) 동작, Windows CI 초록
- [ ] v0.1.0 태그
