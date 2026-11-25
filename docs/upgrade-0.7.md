# 0.6 → 0.7 업그레이드 가이드

0.7.0은 14번째 패키지 **Censor**(`de-censor`, 프로젝트 감사, experimental)를 추가하는 minor 릴리스다.
기존 명령, 옵션, YAML 필드, 저장 형식은 바뀌지 않는다. **상태 DB도 바뀌지 않는다.** StateStore는 v3,
`aquila.db`는 v2 그대로이고 마이그레이션도 백업도 일어나지 않는다.

## 1. 한꺼번에 설치한다

교차 패키지 핀이 `>=0.7,<0.8`로 이동했다. 일부만 올리면 resolver가 거부한다.

```bash
uv tool install --force de-arsenal
uv tool install --force "de-arsenal[streaming,distributed,catalog,dispatch]"
```

스펙 파일과 `.arsenal/` 디렉터리는 손대지 않아도 된다.

## 2. 선택 — Censor

```bash
uv tool install --force de-arsenal --with de-censor   # `arsenal eval`을 쓸 때
uv tool install de-censor                             # `censor` 명령을 따로 쓸 때
pip install de-censor                                 # 둘 다 (de-arsenal도 함께 설치된다)
```

```bash
arsenal eval .                  # 또는 censor audit .
censor report                   # 저장된 실행의 등급 추이
censor compare <run_a> <run_b>  # 상태가 바뀐 체크만
```

- 프로젝트를 실행하지 않는다. 스펙, README, `.claude/` 설정 같은 파일만 읽는다. 네트워크도 쓰지 않는다.
- stdout에는 JSON 리포트(`schemas/censor-report.json`), stderr에는 등급 한 줄이 나온다.
- 종료 코드: 0 실패한 체크 없음, 1 하나 이상 실패, 2 Censor가 실행되지 못함. CI 게이트로 쓸 수 있다.
- 스펙에 평문 비밀값이 있거나 스펙이 로드되지 않으면 등급이 C를 넘지 못한다. 비밀값은 종류와 줄 번호만
  보여 주고 값은 출력하지 않는다.
- 결과는 `.arsenal/censor/`에 JSON, Parquet, Markdown으로 남는다. 이 디렉터리는 스스로를 git에서
  무시한다(`.gitignore`에 `*`). 다른 도구는 이 디렉터리를 읽지 않는다.

설치하지 않으면 아무것도 달라지지 않는다. `arsenal eval`만 설치 안내를 내고 종료 코드 2로 끝난다.

**아직 없는 것**: 실행 상태와 데이터를 읽는 Standard 단계(신뢰성, 신선도, 데이터 프로파일링, Postgres
테이블 대상)는 0.7.0에 없다. `--tier standard`는 안내를 내고 종료 코드 2로 끝난다. experimental이므로 리포트와 이력 형식은
0.x에서 바뀔 수 있다.

## 3. 롤백

패키지를 0.6.17로 다시 설치하면 된다. 0.7이 기존 상태 DB에 쓰는 것이 없으므로 복원할 파일이 없다.
`.arsenal/censor/`는 0.6이 읽지 않으니 남겨 둬도 되고 지워도 된다.
