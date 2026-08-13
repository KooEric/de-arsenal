# v0.2.0 릴리스 절차

## 확정 정책

- 배포 패키지 8개는 v0.2.0으로 동기화한다.
- 루트 workspace 프로젝트는 배포하지 않으므로 `0.0.0`을 유지한다.
- 변환 패키지의 PyPI 배포명은 `de-gladius`다.
- Python import 경로, 소스 디렉터리, CLI 명령은 하위 호환을 위해 `gladius`를 유지한다.
- `Falcata`는 향후 브랜딩 후보이며 v0.2.0 범위에는 포함하지 않는다.

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
배포명은 `de-gladius`여야 한다. 빌드 결과에는 8개 패키지의 sdist와 wheel이
포함되어야 한다.

## GitHub 릴리스

태그를 만들기 전에 Actions 탭에서 `release` workflow가 보이는지 확인한다.
수동 실행은 검증만 하고 PyPI에 게시하지 않는다.

```bash
gh workflow list
gh workflow run release.yml -f version=0.2.0
gh run list --workflow release.yml --limit 1
```

수동 실행의 `verify-and-build`가 성공한 뒤에만 태그를 만든다.

```bash
git tag v0.2.0
git push origin v0.2.0
```

`v*` 태그가 push되면 [release workflow](../.github/workflows/release.yml)가
다음을 순서대로 수행한다.

1. 전체 품질 게이트와 테스트 실행
2. `uv build --all-packages` 실행
3. 빌드 산출물 보관
4. PyPI Trusted Publishing으로 게시

`publish` job은 tag push에서만 실행된다. 수동 `workflow_dispatch` 실행에는
게시 job이 실행되지 않는다.

게시 전 GitHub 저장소의 `pypi` environment와 PyPI Trusted Publisher 설정이
필요하다. API token을 저장소에 넣지 않는다.

## 이름 변경 원칙

`de-gladius`는 배포명만 바꾼 것이므로 기존 사용자는 `import gladius`와
`gladius` 명령을 계속 사용할 수 있다. 향후 `Falcata`로 import 이름까지
바꾸려면 별도 마이그레이션 릴리스와 deprecation 기간을 둔다.

## 실패 시 처리

PyPI에 업로드된 버전은 같은 번호로 덮어쓰지 않는다. 게시 후 오류가 발견되면
수정된 패키지 전체를 다음 패치 버전으로 올려 다시 릴리스한다.
