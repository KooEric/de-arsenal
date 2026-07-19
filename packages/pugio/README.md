# Pugio

> 단검. 가장 작고 늘 휴대하는 일상 도구.

수집·전송(ETL) 엔진. REST API·로컬 파일(csv/jsonl/excel)·운영 DB에서 데이터를
읽어 Parquet/DuckDB/Postgres로 적재한다. 재개·멱등·인증 자동 갱신이 기본값 —
중단 후 재실행하면 마지막 완료 지점부터 이어지고, 같은 unit을 다시 적재해도
중복이 생기지 않는다.

## 설치

```bash
pip install pugio
# postgres sink를 쓴다면:
pip install "pugio[postgres]"
# excel 파일을 수집한다면:
pip install "pugio[excel]"
```

## 사용 예

```yaml
# collect.yaml
name: github-issues
source:
  type: rest
  url: https://api.github.com/repos/duckdb/duckdb/issues
  headers: { Authorization: "Bearer ${GITHUB_TOKEN}" }
  pagination: { mode: offset, param: page, size_param: per_page, size: 100 }
sink:
  type: parquet
  path: ./data/issues
```

```bash
pugio run collect.yaml      # 수집. kill -9 후 재실행해도 이어서 진행
pugio status collect.yaml   # unit 상태 요약
pugio dlq list collect.yaml # 검증 위반으로 격리된 unit 조회
```

## 문서

- [docs/01-scope.md](../../docs/01-scope.md) — Pugio가 다루는 범위(M1·M2)
- [docs/08-limits.md](../../docs/08-limits.md) — sink별 멱등 보장 범위, 알려진 한계
- [examples/quickstart](../../examples/quickstart) — collect → transform → query 10분 데모
- [저장소 루트 README](../../README.md)
