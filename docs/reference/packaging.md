# 패키징 릴리스 체크리스트 (v0.2.1)

이 문서는 v0.2.1 패치 릴리스의 패키징 검증과 PyPI 게시 전 확인사항을 기록한다. 실제
태그 릴리스 절차는 [docs/release.md](../release.md)를 기준으로 한다.

## uv build

```bash
uv build --all-packages
```

8개 패키지(`arsenal-core`, `pugio`, `de-gladius`, `de-arsenal`, `de-scorpio`, `scutum`,
`spatha`, `onager`) 전부 sdist+wheel
생성 성공. wheel 메타데이터에 `License-Expression: Apache-2.0`, Python 3.11/3.12
분류자, `License :: OSI Approved :: Apache Software License` 분류자,
`Project-URL: Homepage`/`Repository`(→ `https://github.com/KooEric/de-arsenal`)가
모두 포함됨을 `unzip`으로 확인.

## 클린 venv 설치 스모크 테스트

**방법**: 워크스페이스 로컬 `path` 의존성(`[tool.uv.sources]`)은 PyPI에 아직
없는 이름을 가리키므로, `uv sync`/`uv pip install -e`류로는 "진짜 배포된 것처럼"
검증할 수 없다. 대신:

1. `uv build --all-packages`로 8개 wheel을 만든다.
2. `uv venv`로 완전히 새 가상환경을 만든다(이 저장소 밖, 스크래치 디렉터리).
3. `uv pip install <필요한 wheel 경로를 동시에>`로 설치 — 이러면 pip가 로컬 파일을
   후보로 각 패키지의 `Requires-Dist`(`arsenal-core>=0.2,<0.3` 등)를
   실제 의존성 리졸버로 풀고, PyPI에서 나머지(`pydantic`, `duckdb`, `httpx`,
   `pyarrow`, `typer` 등)를 내려받는다 — editable/workspace 지름길이 전혀 없다.
4. `arsenal --help` / `pugio --help` / `gladius --help` 각각 실행.

**v0.2.0 결과 (2026-08-11)**: 8개 wheel을 새 가상환경에 동시 설치했다. `arsenal`,
`pugio`, `gladius`의 `--help`가 모두 exit code 0으로 동작했고, `import gladius`는
`0.2.0`을 반환했다. 즉 배포명 `de-gladius`와 기존 import/CLI 경로가 함께 검증됐다.

**검증 중 발견하고 고친 버그**: `pugio/sinks/__init__.py`가 최상단에서
`PostgresSink`를 import하고 있었는데, `postgres` extra(`psycopg`)는 optional
(`pugio[postgres]`)이다. 그 결과 `pugio`(extra 없이) 최소 설치만으로도
`import pugio.cli` → `pugio.runner` → `pugio.sinks` → `pugio.sinks.postgres`
→ `import psycopg`가 연쇄되어 **psycopg 미설치 환경에서 `pugio --help`조차
`ModuleNotFoundError`로 죽었다** — 워크스페이스 루트 `pyproject.toml`이
`pugio[postgres]`를 항상 끌어와서 로컬 개발 환경에서는 이 문제가 가려져 있었다
(정확히 "editable-dependency leakage" 케이스). `sources/file.py`의 `fastexcel`
지연 import 패턴을 `sinks/__init__.py`에도 동일하게 적용해 고쳤다 —
`build_sink`의 `postgres` 분기에서만 실제로 import하고, 정적 타입은
`TYPE_CHECKING` 가드로, `from pugio.sinks import PostgresSink` 같은 기존 코드는
모듈 `__getattr__`(PEP 562)로 지연 해결한다. 수정 후 재빌드+재설치로 위 결과를
재확인했다.

## arsenal-core 버전 핀

`pugio`/`de-gladius`/`de-arsenal` 세 패키지의 `[project] dependencies`에서
`arsenal-core`를 `arsenal-core>=0.2,<0.3`로 고정(스펙 하위 호환 정책과 일치).
워크스페이스 소스 해석(`[tool.uv.sources]`)은 그대로 유지되므로 로컬 개발은
`uv sync`가 여전히 workspace 멤버를 직접 쓴다 — 이 핀은 게시된 메타데이터
전용이다. `uv lock --check`으로 락파일 일관성 확인 완료.

## Windows CI

`.github/workflows/ci.yml`의 `matrix.os`에 `windows-latest` 추가
(`[ubuntu-latest, macos-latest, windows-latest]`). YAML 문법은 로컬에서
파싱 검증했고, 매트릭스 확장 외 다른 스텝은 손대지 않았다.

**검증 못한 것 — 사용자 인프라 필요**: 이 저장소는 macOS에서 작업 중이라
`windows-latest` 러너에서 실제로 그린인지는 로컬에서 확인 불가능하다.
`os.replace` 원자성, 경로 구분자, 인코딩(euc-kr 등) 이슈가 Windows에서
드러날 가능성이 있는 지점(`docs/plans/2026-07-08-m4-integration-release.md`
Task 4.5)이므로, **push 후 실제 GitHub Actions 실행에서 windows-latest job이
녹색인지 확인이 필요하다.**

## PyPI 배포명

```bash
curl -s -o /dev/null -w "%{http_code}" https://pypi.org/pypi/<name>/json
# 404 = 비어있음(사용 가능), 200 = 이미 등록됨
```

| 이름 | HTTP 코드 | 상태 |
|---|---|---|
| `de-arsenal` | 404 | 사용 가능 |
| `pugio` | 404 | 사용 가능 |
| `de-gladius` | 404 (2026-08-11) | 사용 가능 확인. Python import와 CLI는 기존 `gladius` 유지 |
| `scorpio` | 200 | 기존 타 프로젝트가 사용 중이므로 사용하지 않음 |
| `de-scorpio` | 404 | 이 저장소의 배포명으로 선택. Python import는 `scorpio` 유지 |

`gladius` 배포명은 다른 프로젝트가 사용 중이므로 `de-gladius`로 확정했다.
`packages/gladius/src/gladius`와 `gladius` CLI는 변경하지 않는다.
`scorpio` 배포명도 다른 프로젝트가 사용 중이므로 `de-scorpio`로 확정했다.
`packages/scorpio/src/scorpio`와 `scorpio` import는 변경하지 않는다.

## LICENSE / README

- 루트 `LICENSE`: Apache License 2.0 전문, 신규 생성(`Copyright 2026 KooEric`).
- 핵심 4개 패키지 `pyproject.toml`에 `license = "Apache-2.0"`(SPDX 표현식, PEP 639)
  + 표준 classifiers + `project.urls`(Homepage/Repository) 추가.
- 핵심 4개 패키지 각각 `README.md` 신규 작성(무엇인지 1문단 + 사용 예 1개 + 문서 링크).
- P1 패키지(`onager`, `scorpio`, `scutum`, `spatha`)도 `uv build --all-packages`와
  개별 README를 갖춘다. clean-venv 설치 스모크는 이 8개 wheel 조합으로 별도 재실행해야 한다.

## 로컬에서 끝낼 수 없는 것 (사용자 인프라/판단 필요)

1. **Windows CI 그린 확인** — push 후 실제 GitHub Actions 실행 결과를 봐야 함.
2. **PyPI 게시(publish)** — `v0.2.1` 태그 push 후 release workflow가 수행한다.
   사전에 GitHub `pypi` environment와 PyPI Trusted Publisher 설정이 필요하다.
