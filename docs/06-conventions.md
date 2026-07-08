# 06. 개발 컨벤션

## 코드 스타일

- Python 3.11+ 문법, 전체 타입 힌트 필수 (pyright strict 통과)
- ruff로 lint+format (line length 100). 설정은 루트 `pyproject.toml` 단일 소스
- 네이밍: 함수/변수 `snake_case`, 클래스 `PascalCase`, 상수 `UPPER_SNAKE_CASE`, boolean은 `is_/has_/should_` 접두
- **불변 우선**: 스펙 모델은 `frozen=True`, 함수는 입력을 변경하지 않고 새 값 반환. 명백한 지역 누적(리스트 append 등)은 예외
- 파일 200~400줄 권장, 800줄 초과 금지 — 초과 전에 모듈 분리
- 함수 50줄 이하, 중첩 4단계 이하 — 넘으면 early return·함수 추출
- 매직 넘버 금지 — 의미 있는 상수로 (`DEFAULT_PAGE_SIZE = 100`)
- 에러는 절대 삼키지 않는다. 분류된 예외([02-architecture.md](02-architecture.md) 에러 체계)로 변환해 던지고, 러너 층에서만 처리

## 시스템 경계 검증

- 모든 외부 입력(YAML, API 응답, 파일)은 경계에서 검증: YAML→Pydantic, API 응답→스키마 대조
- 비밀은 코드·YAML에 두지 않는다 — `${ENV_VAR}` 치환만 허용. 로그에 토큰·헤더 값 출력 금지

## 커밋

```
<type>: <설명>

<선택 본문>
```

- type: `feat` `fix` `refactor` `docs` `test` `chore` `perf` `ci`
- 예: `feat: add offset pagination to rest source`, `test: cover resume after crash`
- 태스크당 1~3 커밋. 테스트와 구현은 같은 커밋에 (TDD 단위)

## 브랜치·PR

- `main`은 항상 초록(테스트 통과). 직접 push는 문서·M0까지만, 이후 PR
- 브랜치: `feat/m1-state-store`, `fix/rest-encoding` — 마일스톤/태스크 단위
- PR 전 체크: CI 통과, 충돌 해소, `git diff main...HEAD`로 전체 변경 검토
- PR 본문: 무엇을/왜 + 테스트 계획 체크리스트

## 코드 리뷰 기준

| 레벨 | 의미 | 행동 |
|---|---|---|
| CRITICAL | 신뢰성 계약 위반(멱등 깨짐, 상태 유실 가능성), 보안 | 머지 차단 |
| HIGH | 버그, 계약 테스트 누락 | 머지 전 수정 |
| MEDIUM | 유지보수성 | 가능하면 수정 |
| LOW | 스타일 | 선택 |

리뷰 필수 관점: **"이 변경이 kill→재실행 시나리오를 깨뜨리는가?"** — sink·state·runner를 건드리는 모든 PR은 신뢰성 시나리오 테스트 통과가 조건.

## 문서 규칙

- 스펙(YAML 필드) 변경은 코드와 같은 PR에서 문서 갱신 — 선언이 인터페이스이므로 문서 드리프트는 버그
- 아키텍처 결정은 [02-architecture.md](02-architecture.md)의 ADR 표에 한 줄 추가
- 각 마일스톤 착수 시 `docs/plans/YYYY-MM-DD-<milestone>.md` 상세 TDD 계획 작성

## 개발 환경

```bash
# 최초 1회
uv sync                      # 전체 워크스페이스 의존성
uv run pre-commit install

# 일상 루프
uv run pytest packages/arsenal-core -x          # 작업 중 패키지만 빠르게
uv run ruff check --fix . && uv run ruff format .
uv run pyright
uv run pytest                                   # 커밋 전 전체
```
