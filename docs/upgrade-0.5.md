# 0.4 → 0.5 업그레이드 가이드

0.5.0은 12번째 패키지 **Aquila**(`de-aquila`, 데이터셋 카탈로그)를 추가하는 minor
릴리스다. 기존 11개 패키지의 **동작과 상태 DB는 바뀌지 않는다** — StateStore 스키마는
v3 그대로이고 마이그레이션도, 백업도 일어나지 않는다.

## 1. 한꺼번에 설치한다

교차 패키지 핀이 `>=0.5,<0.6`으로 이동했다. 일부만 올리면 resolver가 거부한다.

```bash
uv tool install --force de-arsenal                    # 기본 도구
uv tool install --force "de-arsenal[catalog]"         # + Aquila
uv tool install --force "de-arsenal[streaming,distributed,catalog]"
```

Aquila는 선택 사항이다. 설치하지 않으면 0.4와 똑같이 동작한다.

## 2. 카탈로그를 처음 만든다 (선택)

```bash
cd <arsenal 프로젝트>
aquila scan                 # .arsenal/aquila.db 생성. 재실행해도 행이 늘지 않는다
aquila lineage <데이터셋> --format mermaid
```

`aquila scan`은 파이프라인 상태 DB를 **읽기 전용**으로 연다. 실행 중인 러너와 동시에
돌려도 잠금·마이그레이션·백업을 일으키지 않는다. `aquila.db`를 지우면 카탈로그만 사라지고
파이프라인은 영향이 없다.

## 3. 동작이 바뀐 것 — `de-arsenal[catalog]`와 `aquila.db`가 둘 다 있을 때만

- **`arsenal status`** 출력에 `catalog` 블록이 붙는다. 선언된 계약과 최신 스키마가 어긋나면
  `alert: true`, exit code 1이 된다. 상태 점검을 CI·cron에 걸어 두었다면, 처음 `aquila scan`을
  돌린 뒤 `aquila show <데이터셋>`으로 계약 불일치를 먼저 정리한다.
- **`augur index`**를 소스 파일 없이 실행하면 `.arsenal/aquila.db`의 활성 Parquet 데이터셋을
  읽는다. 소스 YAML을 주면 0.4와 같다.

## 4. 롤백

0.4로 되돌릴 때 할 일은 패키지를 다시 설치하는 것뿐이다. 0.5가 기존 상태 DB에 쓰는 것이
없으므로 복원할 파일이 없다. `.arsenal/aquila.db`는 0.4가 읽지 않으니 남겨 둬도 된다.

보장·비보장 범위: [08-limits.md](08-limits.md#aquila-카탈로그의-보장과-한계-05-preview)
