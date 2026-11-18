# 크로스 패키지 테스트

- `integration/` — 실제 컴포넌트 조합 (SQLite·DuckDB 실물, HTTP는 respx) — M1부터
- `e2e/` — 신뢰성 시나리오 테스트: kill→재개, 멱등 재실행 (docs/05-testing-plan.md의 심장) — M1 Task 10부터

## Postgres가 필요한 테스트

기본은 Docker로 `postgres:16-alpine` 컨테이너를 띄우고, Docker가 없으면 skip한다. 이미 떠 있는
Postgres를 쓰려면 데이터베이스를 만들 수 있는 계정의 URL을 준다. 테스트 모듈마다 새
데이터베이스를 만들고 끝나면 지운다.

```bash
ARSENAL_TEST_POSTGRES_URL=postgresql://postgres:pw@localhost:5432/postgres uv run pytest
```
