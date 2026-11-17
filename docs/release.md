# v0.4.0 릴리스 절차

P2 릴리스다. 새 실행 도구 두 개(**Hasta** 스트리밍·CDC, **Ballista** 분산 변환)가
공통 실행 계약(`arsenal-core`의 StateStore v3, fenced lease, 출력 증명) 위에 올라가고,
실험 패키지 **Augur**가 함께 처음 배포된다. 전체 내역은 [CHANGELOG](../CHANGELOG.md),
사용자 업그레이드 절차는 [upgrade-0.4.md](upgrade-0.4.md).

**minor인 이유**: StateStore가 forward-only로 v3까지 마이그레이션된다(0.4가 연 DB는
다운그레이드 경로가 없다). `onager compact`가 스트림이 쓰는 데이터셋에서 거부할 수
있게 됐다. 0.x에서 minor가 파괴적 축이므로 교차 패키지 핀을 `>=0.4,<0.5`로 옮긴다.
배치 파이프라인의 unit id·sink 파일명·변환 SQL은 바뀌지 않는다.

## 확정 정책

- 배포 패키지 11개를 v0.4.0으로 동기화한다: `arsenal-core`, `de-arsenal`, `pugio`,
  `de-gladius`, `de-scorpio`, `de-scutum`, `de-onager`, `spatha`, **`de-augur`**,
  **`de-hasta`**, **`de-ballista`** (굵은 셋은 PyPI 첫 게시).
- 교차 패키지 의존 범위는 `>=0.4,<0.5`.
- 루트 workspace 프로젝트는 배포하지 않으므로 `0.0.0`을 유지한다.
- import 경로·CLI 이름은 그대로다(`gladius`, `scorpio`, `scutum`, `onager`, `hasta`, `ballista`, `augur`).
- `de-arsenal` 기본 설치는 Spark·Kubernetes·Iceberg·Kafka·obstore·psycopg를 끌어오지
  않는다. Hasta·Ballista는 `de-arsenal[streaming]`·`de-arsenal[distributed]` extra,
  Ballista의 클러스터 런타임은 다시 `de-ballista[spark-kubernetes,iceberg,object-store]`.
- Hasta·Ballista는 **beta**, Augur는 **experimental**로 표기한다.

## 머지 순서

P2 작업은 쌓인 PR 네 개다. 각각 CI green 확인 후 순서대로 머지한다.

1. #14 `feat/p2-hasta-ballista` → `main` (Phase 0: P2 골격, 0.4.0 버전)
2. #15 `feat/p2-phase1-ballista` → #14 브랜치 (Ballista 실클러스터 연동, Spark corpus, kind E2E)
3. #16 `feat/p2-phase2-hasta` → #14 브랜치 (Hasta 운영 명령, 10만 이벤트 재개 증명)
4. #17 `feat/p2-phase3-integration` → #16 브랜치 (Scorpio·Onager·Spatha·Arsenal 통합)
5. 릴리스 PR (이 문서, CHANGELOG, README) → 위 전부가 합쳐진 `main`

\#15와 #16은 서로 독립이다. 쌓인 PR의 base 브랜치가 먼저 머지되면 GitHub가 다음 PR의
base를 자동으로 바꾸지 않을 수 있으니, 머지 후 base가 `main`을 가리키는지 확인한다.

## 태그 전 검증

```bash
uv sync
uv run python scripts/check_release_metadata.py   # "11 packages at v0.4.0"
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run python scripts/gen_schema_docs.py --check
uv run pytest
uv build --all-packages
ls dist/ | wc -l                                  # sdist + wheel × 11 = 22
```

추가로 CI에서 확인할 것:

- `ci.yml` — 3 OS × Python 3.11/3.12 green, `portable-equivalence`(Java 17 + pyspark 3.5.3) green.
- `ballista-e2e.yml` — kind + MinIO + Iceberg REST 실행이 `main` 기준 green.
  수동 실행: `gh workflow run ballista-e2e.yml`.

릴리스 전 수동 점검 체크리스트:

- [ ] `pip install de-arsenal`(새 venv)에 `pyspark`/`kubernetes`/`pyiceberg`가 없다
      (`packages/arsenal/tests/test_base_install.py`가 workspace에서 같은 것을 확인).
- [ ] 0.3.0으로 만든 상태 DB를 0.4로 열면 `backups/`에 백업이 생기고 `schema_version == 3`.
- [ ] `examples/hasta` quickstart가 README대로 돈다(`tests/e2e/test_hasta_quickstart.py`).
- [ ] CHANGELOG의 `## [0.4.0] - TBD`를 실제 날짜로 바꾼다.

## PyPI 준비 — 신규 배포명 3개 (계정 소유자)

`de-augur`, `de-hasta`, `de-ballista`는 PyPI에 아직 없다. Trusted Publishing으로 처음
만들려면 PyPI 계정의 **Publishing → Pending publishers**에 셋을 각각 등록한다.

| 필드 | 값 |
|---|---|
| PyPI project name | `de-augur` / `de-hasta` / `de-ballista` |
| Owner / Repository | `KooEric` / `de-arsenal` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

GitHub 저장소의 `pypi` environment는 0.3.0 때 만든 것을 그대로 쓴다. API token은
저장소에 넣지 않는다.

## GitHub 릴리스

먼저 검증만 하는 수동 실행으로 빌드가 되는지 본다.

```bash
gh workflow run release.yml -f version=0.4.0 -f publish_package=none
gh run list --workflow release.yml --limit 1
```

`verify-and-build`가 성공하면 태그를 만든다.

```bash
git tag v0.4.0
git push origin v0.4.0
```

`v*` 태그 push에서 [release workflow](../.github/workflows/release.yml)가 전체 게이트와
테스트 → `uv build --all-packages` → 산출물 보관 → PyPI Trusted Publishing 순으로 실행한다.
pending publisher 셋이 등록돼 있으면 태그 실행 한 번으로 11개가 모두 게시되고,
신규 셋은 이때 PyPI 프로젝트가 생성된다. 이미 게시된 파일은 `skip-existing`으로 건너뛴다.

**대안 — 하나씩 부트스트랩:** 태그 실행에서 신규 패키지만 실패하면(pending publisher
누락 등) 등록을 고친 뒤 패키지 하나씩 수동 게시한다.

```bash
gh workflow run release.yml -f version=0.4.0 -f publish_package=de-augur
gh workflow run release.yml -f version=0.4.0 -f publish_package=de-hasta
gh workflow run release.yml -f version=0.4.0 -f publish_package=de-ballista
```

## 게시 후 확인

```bash
uv tool install --force "de-arsenal[streaming,distributed]==0.4.0"
arsenal --help && hasta --help && ballista --help
```

- PyPI 11개 프로젝트 페이지가 0.4.0을 보이고, README가 렌더된다.
- GitHub Release 노트에 CHANGELOG 0.4.0 항목과 upgrade-0.4.md 링크를 붙인다.

## 실패 시 처리

PyPI에 업로드된 버전은 같은 번호로 덮어쓰지 않는다. 일부 패키지만 게시된 상태에서
결함이 발견되면, 나머지를 같은 0.4.0으로 마저 게시하지 말고 수정 후 **11개 전체를
0.4.1로** 다시 릴리스한다(핀이 `>=0.4,<0.5`라 0.4.1이 자동으로 선택된다). 0.4.0
자체는 PyPI에서 yank해 새 설치가 고르지 않게 한다.

## 이름 원칙

배포명만 `de-` 접두어를 쓰고 import·CLI 이름은 짧은 이름을 유지한다. `Falcata` 등
import 이름 변경은 별도 마이그레이션 릴리스와 deprecation 기간을 둔다.
