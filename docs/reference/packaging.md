# 패키징 릴리스 체크리스트 (M4 Task 4.5)

이 문서는 v0.1.0 패키징 검증(로컬에서 할 수 있는 것)의 결과와, 사용자(레포
소유자)의 인프라·판단이 필요한 나머지를 구분해 기록한다.

## uv build

```bash
uv build --all-packages
```

8개 패키지(`arsenal-core`, `pugio`, `gladius`, `de-arsenal`, `scorpio`, `scutum`,
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
   후보로 각 패키지의 `Requires-Dist`(`arsenal-core>=0.1,<0.2` 등)를
   실제 의존성 리졸버로 풀고, PyPI에서 나머지(`pydantic`, `duckdb`, `httpx`,
   `pyarrow`, `typer` 등)를 내려받는다 — editable/workspace 지름길이 전혀 없다.
4. `arsenal --help` / `pugio --help` / `gladius --help` 각각 실행.

**기존 결과**: 핵심 4개 wheel 설치 성공(26개 패키지, PyPI에서 실제 다운로드). 세 CLI 모두
`--help` 정상 출력, exit code 0.

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

`pugio`/`gladius`/`de-arsenal` 세 패키지의 `[project] dependencies`에서
`arsenal-core`를 `arsenal-core>=0.1,<0.2`로 고정(스펙 하위 호환 정책과 일치).
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

## PyPI 이름 확인

```bash
curl -s -o /dev/null -w "%{http_code}" https://pypi.org/pypi/<name>/json
# 404 = 비어있음(사용 가능), 200 = 이미 등록됨
```

| 이름 | HTTP 코드 | 상태 |
|---|---|---|
| `de-arsenal` | 404 | 사용 가능 |
| `pugio` | 404 | 사용 가능 |
| `gladius` | **200** | **선점됨** — "Gladius" (Tangled Group, Inc, 순수 Python 웹앱 프레임워크, 최신 0.3.5). 우리 `gladius`와 무관한 기존 패키지. |

**`gladius`는 이름이 선점되어 있다.** 지금 패키지 이름을 바꾸지는 않는다(범위
밖 결정) — PyPI에 실제로 배포하려는 시점에 아래 중 하나를 **사용자가 결정**해야
한다:

- 접두 전략으로 배포명만 변경: 예 `arsenal-gladius` (import 이름 `gladius`는
  유지, `[project] name`만 바꾸는 방식 — pugio에는 아직 충돌이 없으니 일관성을
  위해 `arsenal-pugio`도 함께 바꿀지 결정 필요)
  * 참고로 `de-arsenal` 배포명은 이미 `arsenal-*` 접두 관례가 아니라 `de-`
    접두라 완전히 통일하기는 어렵다 — 명명 규칙 자체를 재검토할 필요가 있음.
- 또는 PyPI에 문의해 이름 이전/분쟁 절차를 밟는다(가능성 낮음, 시간 소요).

## LICENSE / README

- 루트 `LICENSE`: Apache License 2.0 전문, 신규 생성(`Copyright 2026 KooEric`).
- 핵심 4개 패키지 `pyproject.toml`에 `license = "Apache-2.0"`(SPDX 표현식, PEP 639)
  + 표준 classifiers + `project.urls`(Homepage/Repository) 추가.
- 핵심 4개 패키지 각각 `README.md` 신규 작성(무엇인지 1문단 + 사용 예 1개 + 문서 링크).
- P1 패키지(`onager`, `scorpio`, `scutum`, `spatha`)도 `uv build --all-packages`와
  개별 README를 갖춘다. clean-venv 설치 스모크는 이 8개 wheel 조합으로 별도 재실행해야 한다.

## 로컬에서 끝낼 수 없는 것 (사용자 인프라/판단 필요)

1. **Windows CI 그린 확인** — push 후 실제 GitHub Actions 실행 결과를 봐야 함.
2. **PyPI 게시(publish)** — 이 태스크는 `uv build`까지만. `gladius` 이름 충돌
   해소 결정이 선행돼야 하고, 실제 업로드(`uv publish`/`twine`)는 계정·토큰이
   필요한 별도 단계로 이 세션에서 수행하지 않았다.
