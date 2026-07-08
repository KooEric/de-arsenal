# github-issues 레시피

GitHub 저장소의 이슈를 받아 정리된 테이블로 만든다.

1. 바꿀 것: `collect.yaml`의 저장소 경로, 환경변수 `GITHUB_TOKEN`
2. 실행: `arsenal run`
3. 확인: `arsenal query "SELECT state, count(*) FROM './data/issues_clean/*.parquet' GROUP BY 1"`
