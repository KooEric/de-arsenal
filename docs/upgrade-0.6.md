# 0.5 → 0.6 업그레이드 가이드

0.6.0은 13번째 패키지 **Pilum**(`de-pilum`, reverse ETL, experimental)을 추가하고, 파일
소스의 동작 세 가지를 바꾸는 minor 릴리스다. **상태 DB는 바뀌지 않는다.** StateStore는
v3, `aquila.db`는 v2 그대로이고 마이그레이션도 백업도 일어나지 않는다.

## 1. 한꺼번에 설치한다

교차 패키지 핀이 `>=0.6,<0.7`로 이동했다. 일부만 올리면 resolver가 거부한다.

```bash
uv tool install --force de-arsenal
uv tool install --force "de-arsenal[streaming,distributed,catalog,dispatch]"
```

## 2. 올리기 전에 확인할 것 — `type: file` 소스를 쓰는 파이프라인

| 바뀐 동작 | 영향받는 경우 | 할 일 |
|---|---|---|
| `path`에 매칭되는 파일이 없으면 에러 (전에는 `fetched=0`으로 성공) | 비어 있을 수 있는 폴더를 주기적으로 수집 | `source.allow_empty: true` |
| 앞자리 0이 있는 CSV 컬럼은 문자열로 적재 (전에는 `02139` → `2139`) | 우편번호·사번·계좌번호 같은 컬럼, 또는 0으로 채운 숫자 | 아래 절차 |
| 한 실행의 파일들 사이에 컬럼 타입이 다르면 경고, `schema_drift: block`이면 실패 | 파일마다 형식이 조금씩 다른 CSV | `source.column_types`로 타입 고정 |

앞자리 0 컬럼이 있었는지 확인하는 방법: 0.5로 적재한 Parquet에서 그 컬럼이 정수 타입이면
해당된다.

```bash
gladius query "DESCRIBE SELECT * FROM './data/raw/**/*.parquet'"
```

- **값이 원래 식별자였다면**(우편번호 등) 이미 적재된 정수에서는 0을 복구할 수 없다. 원본 CSV가
  남아 있으면 0.6으로 다시 수집한다: 상태 DB(`.arsenal/<name>.db`)와 출력 디렉터리를 지우고
  `pugio run`을 다시 실행한다. Postgres·DuckDB sink라면 대상 컬럼 타입을 먼저 `text`로 바꾼다.
- **값이 정말 숫자라면**(`007`이 7을 뜻함) 타입을 고정해 0.5와 같은 결과를 유지한다.

  ```yaml
  source:
    type: file
    path: ./input/*.csv
    column_types: { seq: int64 }
  ```

0.5로 쓴 파일(정수)과 0.6으로 쓴 파일(문자열)이 한 디렉터리에 섞이면 `union_by_name` 읽기에서
컬럼 전체가 문자열이 되고, 옛 파일의 값은 0이 빠진 채로 남는다. 섞지 말고 위 둘 중 하나를 고른다.

### 같은 이름으로 덮어써지는 파일

파일은 경로로 한 번만 수집된다. 0.5와 같다. 매일 같은 이름으로 내려받는 `export.csv`처럼
내용이 바뀌는 파일은 다시 읽히지 않는다. 다시 읽게 하려면 upsert sink(`postgres`·`duckdb`)와
함께 아래를 쓴다.

```yaml
source: { type: file, path: ./input/*.csv, detect_changes: true }
```

켜는 순간 unit 식별자가 바뀌므로 이미 수집한 파일도 한 번 다시 읽는다. upsert라 결과는 같다.

### Postgres sink

날짜, 시간대 없는 timestamp, 작은 정수, 값이 전부 빈 컬럼이 있는 CSV가 이제 적재된다
(0.5에서는 `unsupported arrow type` 에러). 시간대 없는 timestamp는 `timestamp` 컬럼에 적힌
시각 그대로 들어간다. 0.5에서 이 에러를 피하려고 변환 단계를 넣었다면 빼도 된다.

## 3. 선택 — 페이지 크기 상한이 있는 API

`offset`·`page` 모드는 `size`보다 적은 행이 온 첫 페이지에서 멈춘다. 서버가 페이지 크기를
`size`보다 작게 제한하면 첫 페이지가 곧 그 페이지다. 수집 건수가 API의 전체 건수와 맞지 않으면
아래를 추가한다. 요청이 한 번 늘어난다.

```yaml
pagination: { mode: page, param: page, size_param: per_page, size: 100, stop_on: empty_page }
```

기본값은 0.5와 같은 `short_page`다.

## 4. 선택 — Pilum

```bash
uv tool install --force "de-arsenal[dispatch]"    # 또는 pip install de-pilum
pilum plan dispatch.yaml      # 보낼 건수와 키 샘플. 아무것도 보내거나 쓰지 않는다
pilum run dispatch.yaml
```

설치하지 않으면 아무것도 달라지지 않는다. 원장은 `.arsenal/<name>.pilum.db`에 따로 생기고
다른 도구는 이 파일을 읽지 않는다. experimental이므로 스펙과 원장 형식은 0.x에서 바뀔 수 있다.
보장 범위: [08-limits.md](08-limits.md#pilum-reverse-etl의-보장과-한계-06-experimental).

## 5. 상태 표기

Onager·Scutum·Scorpio·Spatha의 표기가 stable에서 beta로 바뀌었다. 코드와 API는 그대로다.
이 저장소 밖 사용 사례가 적다는 사실을 표기에 반영한 것이다.

## 6. 롤백

패키지를 0.5.3으로 다시 설치하면 된다. 0.6이 기존 상태 DB에 쓰는 것이 없으므로 복원할 파일이
없다. 0.6에서 추가된 스펙 필드(`column_types`, `allow_empty`, `stop_on`)는 0.5가 거부하므로
YAML에서 지운다. `.arsenal/*.pilum.db`는 0.5가 읽지 않으니 남겨 둬도 된다.
