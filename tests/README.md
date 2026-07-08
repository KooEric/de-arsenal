# 크로스 패키지 테스트

- `integration/` — 실제 컴포넌트 조합 (SQLite·DuckDB 실물, HTTP는 respx) — M1부터
- `e2e/` — 신뢰성 시나리오 테스트: kill→재개, 멱등 재실행 (docs/05-testing-plan.md의 심장) — M1 Task 10부터
