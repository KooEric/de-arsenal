# M4: 통합·릴리스 (v0.1.0) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **초안 상태**: M1~M3 완료 후 실제 산출물 기준으로 갱신해 실행한다. 이 마일스톤은 코드보다 검증·문서가 주라 태스크 수준으로 유지한다.

**Goal:** 신규 사용자가 README만 보고 10분 안에 시나리오 A~D를 재현할 수 있는 v0.1.0.

---

### Task 4.1: 연계 예제 — 수집→변환 end-to-end

**Files:**
- Create: `examples/quickstart/` (README.md + collect.yaml + transform.yaml)
- Create: `tests/e2e/test_quickstart.py`

- [ ] respx 목이 아닌 **로컬 파일 fixture API**(pytest 내 uvicorn 없이, respx로 충분하면 respx)로 quickstart 예제와 동일한 흐름을 자동 검증: `pugio run collect.yaml` → `gladius run transform.yaml` → 결과 행 검증.
- [ ] quickstart README: 설치(`pip install pugio gladius`)부터 결과 확인까지 복붙 가능한 명령만으로 구성. 중간에 "10분" 초과 요소(도커, 계정 생성 등)가 없어야 한다.
- [ ] Commit: `docs: quickstart example with verified e2e`

### Task 4.2: YAML 레퍼런스 자동 생성

**Files:**
- Create: `scripts/gen_schema_docs.py`, `docs/reference/pipeline-schema.md`, `docs/reference/transform-schema.md`

- [ ] `PipelineSpec.model_json_schema()` / `TransformSpec.model_json_schema()`를 마크다운 표로 렌더 — 필드·타입·기본값·설명이 코드에서 나온다(문서 드리프트 = 버그 원칙의 자동화).
- [ ] JSON Schema 파일도 함께 출력(`schemas/*.json`) — 에디터 자동완성용 (`# yaml-language-server: $schema=` 헤더를 예제에 추가).
- [ ] CI에 생성물 최신성 체크 추가: `python scripts/gen_schema_docs.py --check`.
- [ ] Commit: `docs: generated yaml reference + editor schemas`

### Task 4.3: 정직한 한계선 문서

**Files:**
- Create: `docs/08-limits.md`

- [ ] 내용 (docs/07 "줄여주지 못하는 비용"과 벤치마크 실측 기반):
  - 단일 노드 처리 한계 — M3 벤치마크 수치로 "이 규모까지는 이 시간" 표
  - 미지원: 실시간 스트리밍(P2 Hasta), 분산(P2 Ballista), 스키마 드리프트 정책(P1)
  - sink별 멱등 보장 범위 표 (parquet: 파일 단위 / duckdb·pg: 키 단위 upsert)
- [ ] Commit: `docs: honest limits documentation`

### Task 4.4: 패키징 검증

- [ ] `uv build --all-packages` 성공, wheel 메타데이터(license, classifiers, urls) 점검
- [ ] 깨끗한 venv에서 wheel 설치 → `pugio --help`, `gladius --help` 동작 확인 (editable 의존 누수 검출)
- [ ] `arsenal-core` 버전 핀: pugio/gladius가 `arsenal-core>=0.1,<0.2`로 의존 (스펙 하위 호환 정책과 일치)
- [ ] LICENSE(Apache-2.0) 확정, 각 패키지 README 작성
- [ ] Commit: `chore: packaging metadata for v0.1.0`

### Task 4.5: 릴리스

- [ ] CHANGELOG.md 작성 (M1~M4 요약, 스펙 필드 목록)
- [ ] 전체 게이트 + e2e + `RUN_LIVE=1` 스모크 1회
- [ ] `git tag v0.1.0` — PyPI 배포는 태그 후 별도 판단(계정·이름 선점 확인 선행)
- [ ] 10분 테스트: 문서만 보고 quickstart를 처음부터 재현 (실패 지점은 곧 문서 버그)

## M4 DoD

- [ ] 신규 사용자 10분 재현 (시나리오 A~D)
- [ ] 생성 문서·스키마가 CI에서 최신성 보장
- [ ] wheel 설치만으로 두 CLI 동작
- [ ] v0.1.0 태그
