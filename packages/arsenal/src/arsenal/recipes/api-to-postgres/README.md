# api-to-postgres 레시피

REST API를 받아 검증 게이트를 통과시킨 뒤 Postgres에 멱등 upsert한다.

0. 설치: 이 레시피는 postgres extra(`psycopg`)가 필요하다 —
   `uv tool install "de-arsenal[postgres]"` (또는 `pip install "pugio[postgres]"`).
   extra 없이 실행하면 postgres sink가 `FatalError`로 즉시 안내한다(모듈을 찾을 수
   없다는 원문 traceback이 아니다).
1. 바꿀 것: `collect.yaml`의 API URL, 환경변수 `PG_DSN`(Postgres 접속 문자열), `merge_key`
2. 실행: `arsenal run` — 실행 전 `PG_DSN`이 가리키는 Postgres에 접속 가능해야 한다
3. 확인: 위반 레코드는 적재되지 않고 `.arsenal/dlq/api-to-postgres/`에 격리된다 (`pugio dlq list`)
