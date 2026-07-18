# api-to-postgres 레시피

REST API를 받아 검증 게이트를 통과시킨 뒤 Postgres에 멱등 upsert한다.

1. 바꿀 것: `collect.yaml`의 API URL, 환경변수 `PG_DSN`(Postgres 접속 문자열), `merge_key`
2. 실행: `arsenal run` — 실행 전 `PG_DSN`이 가리키는 Postgres에 접속 가능해야 한다
3. 확인: 위반 레코드는 적재되지 않고 `.arsenal/dlq/api-to-postgres/`에 격리된다 (`pugio dlq list`)
