# v0.2.2 패치 릴리스 절차

`v0.2.0`에서 `pugio`가 기존 PyPI 프로젝트인 `scorpio`를 의존하도록 게시된 것을
수정하는 패치 릴리스다. 새 배포명 `de-scorpio`를 만들고, `pugio`와 `de-arsenal`은
같은 버전으로 다시 게시한다.

`v0.2.1` 검증에서 `pugio`의 런타임 `scutum` 의존성 누락이 발견되어 이 패치에서
배포 메타데이터에 `scutum`을 추가한다.

## 확정 정책

- 배포 패키지 8개는 v0.2.2로 동기화한다.
- 루트 workspace 프로젝트는 배포하지 않으므로 `0.0.0`을 유지한다.
- 변환 패키지의 PyPI 배포명은 `de-gladius`다.
- 운영 관측 패키지의 PyPI 배포명은 `de-scorpio`다.
- `de-scorpio`도 import 경로와 기존 모듈명 `scorpio`를 유지한다.
- Python import 경로, 소스 디렉터리, CLI 명령은 하위 호환을 위해 `gladius`를 유지한다.
- `Falcata`는 향후 브랜딩 후보이며 v0.2.2 범위에는 포함하지 않는다.

## 태그 전 검증

```bash
uv sync
uv run python scripts/check_release_metadata.py
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run python scripts/gen_schema_docs.py --check
uv run pytest
uv build --all-packages
```

모든 패키지의 버전은 같아야 하며, `packages/gladius/pyproject.toml`의
배포명은 `de-gladius`와 `de-scorpio`여야 한다. 빌드 결과에는 8개 패키지의 sdist와 wheel이
포함되어야 한다.

## GitHub 릴리스

태그를 만들기 전에 Actions 탭에서 `release` workflow가 보이는지 확인한다.
`publish_package=none`인 수동 실행은 검증만 한다. PyPI 신규 프로젝트를
Trusted Publisher로 처음 만들 때만 `publish_package`에 패키지 하나를 선택한다.

```bash
gh workflow list
gh workflow run release.yml -f version=0.2.2 -f publish_package=none
gh run list --workflow release.yml --limit 1
```

수동 실행의 `verify-and-build`가 성공한 뒤에만 태그를 만든다.

```bash
git tag v0.2.2
git push origin v0.2.2
```

`v*` 태그가 push되면 [release workflow](../.github/workflows/release.yml)가
다음을 순서대로 수행한다.

1. 전체 품질 게이트와 테스트 실행
2. `uv build --all-packages` 실행
3. 빌드 산출물 보관
4. PyPI Trusted Publishing으로 게시

`publish` job은 tag push에서 실행된다. 단, 신규 PyPI 프로젝트를 순서대로
만들어야 하는 최초 부트스트랩 기간에는 수동 실행에서 패키지 하나를 지정해
그 패키지만 게시할 수 있다. 이미 게시된 파일은 `skip-existing`으로 재실행 시
건너뛴다.

현재 v0.2.2 부트스트랩 순서:

1. PyPI 계정의 **Pending publishers**에 `arsenal-core`를 등록한다.
2. `gh workflow run release.yml -f version=0.2.2 -f publish_package=arsenal-core`를 실행한다.
3. `arsenal-core`가 생성되면 PyPI의 동일 publisher를 `de-arsenal`에 등록하고,
   같은 방식으로 나머지 패키지를 하나씩 게시한다.
4. 8개 패키지가 모두 PyPI에 존재하면 `publish_package=none`으로 검증한 뒤
   `v0.2.2` 태그를 push한다. 태그 실행은 이미 존재하는 파일을 건너뛰고
   누락된 파일만 보완한다.

게시 전 GitHub 저장소의 `pypi` environment와 PyPI Trusted Publisher 설정이
필요하다. API token을 저장소에 넣지 않는다.

## 이름 변경 원칙

`de-gladius`는 배포명만 바꾼 것이므로 기존 사용자는 `import gladius`와
`gladius` 명령을 계속 사용할 수 있다. 향후 `Falcata`로 import 이름까지
바꾸려면 별도 마이그레이션 릴리스와 deprecation 기간을 둔다.

## 실패 시 처리

PyPI에 업로드된 버전은 같은 번호로 덮어쓰지 않는다. 게시 후 오류가 발견되면
수정된 패키지 전체를 다음 패치 버전으로 올려 다시 릴리스한다.
