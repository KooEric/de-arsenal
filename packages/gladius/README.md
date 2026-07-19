# Gladius

> 군단병 주력검. 가장 자주 쓰는 변환·쿼리 도구.

선언형 변환(`map`/`steps`)을 SQL로 컴파일해 DuckDB에서 실행하는 엔진. 필터·
리네임·캐스팅·정렬·중복 제거 같은 흔한 변환을 YAML 레시피로 표현하면, 손으로
쓴 SQL과 동일한 결과를 벡터화 엔진으로 빠르게 낸다. `gladius compile`로 생성된
SQL을 그대로 확인할 수 있어 마법이 없다. `gladius query`로 수집 결과 Parquet에
바로 SQL을 던지는 미니 DWH로도 쓴다.

## 설치

```bash
pip install gladius
```

## 사용 예

```yaml
# transform.yaml
name: clean-issues
input: ./data/issues
steps:
  - filter: "state = 'open'"
  - rename: { created_at: opened_at }
  - cast: { number: bigint }
  - select: [number, title, opened_at, user]
output: ./data/issues_clean
```

```bash
gladius compile transform.yaml   # 생성될 SQL을 미리 확인
gladius run transform.yaml       # Parquet in → DuckDB → Parquet out
gladius query "SELECT count(*) FROM './data/issues_clean/*.parquet'"
```

## 문서

- [docs/01-scope.md](../../docs/01-scope.md) — Gladius가 다루는 범위(M3)
- [docs/08-limits.md](../../docs/08-limits.md) — 단일 노드 처리 envelope(벤치마크 실측)
- [benchmarks/RESULTS.md](../../benchmarks/RESULTS.md) — 변환 벤치마크 수치
- [저장소 루트 README](../../README.md)
