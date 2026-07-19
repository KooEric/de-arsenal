# arsenal-core

DE Arsenal(Pugio·Gladius·Arsenal)이 공유하는 신뢰성 코어 라이브러리 —
파이프라인/변환 스펙 로더(Pydantic), 결정적 Unit ID, `StateStore`(SQLite
기반 unit 상태 기록), 에러 분류 체계(`RetryableError`/`AuthExpiredError`/
`FatalError`), backoff 재시도 래퍼. 그 자체로는 CLI가 없는 라이브러리 패키지다
— Pugio/Gladius가 이 위에서 "재개·멱등·에러 분류가 기본값"을 구현한다.

## 사용 예

```python
from arsenal_core.state import UnitSpec, StateStore
from arsenal_core.errors import RetryableError, FatalError

# 결정적 unit_id: 같은 (pipeline, source, unit_key) → 항상 같은 id → 재실행 시 중복 방지
unit = UnitSpec.create(pipeline="github-issues", source="issues", unit_key="page=1")

store = StateStore("./state/github-issues.db")
if not store.is_done(unit.unit_id):
    ...  # fetch → write
    store.mark_done(unit.unit_id, row_count=100, byte_count=8192)
store.close()
```

## 문서

- [docs/02-architecture.md](../../docs/02-architecture.md) — 공통 코어·상태 모델·멱등 전략
- [docs/08-limits.md](../../docs/08-limits.md) — 정직한 한계선
- [저장소 루트 README](../../README.md)
