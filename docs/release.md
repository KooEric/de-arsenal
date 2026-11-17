# v0.5.1 패치 릴리스 절차

데이터 정확성 수정 하나를 담은 패치다. `arsenal.yaml`의 `distributed:` 단계에서
`input_version: auto`가 run id를 리터럴 `"auto"`로 만들어, 입력이 바뀌어도 이미 성공한
첫 Ballista run만 reconcile하고 새 데이터를 처리하지 않던 문제를 고친다(#20).
내역은 [CHANGELOG](../CHANGELOG.md#051---2026-11-17).

**patch인 이유**: 공개 API·스펙 필드·상태 DB가 그대로이고 동작 변경은 버그 수정뿐이다.
12개 패키지를 0.5.1로 함께 올리고 교차 핀은 `>=0.5,<0.6`을 유지한다 — 0.5.0 사용자는
업그레이드만 하면 받는다. 신규 PyPI 프로젝트가 없으므로 pending publisher 등록·수동
부트스트랩도 없다.

## 절차

1. #20 `fix/distributed-auto-input`(수정 + 0.5.1 버전·문서) CI green 확인 후 `main`에 머지
2. `main`에서 검증

   ```bash
   uv sync
   uv run python scripts/check_release_metadata.py   # "12 packages at v0.5.1"
   uv run ruff check . && uv run ruff format --check .
   uv run pyright
   uv run python scripts/gen_schema_docs.py --check
   uv run pytest
   uv build --all-packages && ls dist/ | wc -l       # 24
   ```

3. CHANGELOG의 `## [0.5.1] - TBD`를 실제 날짜로 바꾼다.
4. 검증 전용 수동 실행 후 태그

   ```bash
   gh workflow run release.yml -f version=0.5.1 -f publish_package=none
   git tag v0.5.1 && git push origin v0.5.1
   ```

5. 게시 확인 — 12개 모두 `200`, 그리고 새 venv 설치

   ```bash
   for p in arsenal-core de-arsenal pugio de-gladius de-scorpio de-scutum de-onager spatha \
            de-augur de-hasta de-ballista de-aquila; do
     curl -s -o /dev/null -w "$p %{http_code}\n" "https://pypi.org/pypi/$p/0.5.1/json"
   done
   uv tool install --force "de-arsenal[streaming,distributed,catalog]==0.5.1"
   ```

6. GitHub Release `v0.5.1`을 만들고 CHANGELOG 0.5.1 항목을 붙인다. 아직 없는
   `v0.4.0`·`v0.5.0` Release 페이지도 이때 함께 만든다(태그는 이미 있다).

   ```bash
   gh release create v0.5.1 --title v0.5.1 --notes-file <(sed -n '/^## \[0.5.1\]/,/^## \[0.5.0\]/p' CHANGELOG.md | sed '$d')
   ```

실패 시 처리는 아래 0.5.0 절차와 같다(같은 번호 재게시 불가 → 0.5.2).

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
