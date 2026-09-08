# Augur (`de-augur`)

> 점술관. 자연어를 SQL로 옮기되, 근거(스키마)를 대고, 틀리면 왜 틀렸는지 센다.

Parquet 레이크에 자연어로 질문한다. 스키마 RAG(테이블·컬럼·샘플값 검색) → LLM →
DuckDB 실행. 모든 호출은 trace(JSONL)로 남고, `augur eval`은 골든 SQL과 실행 결과를
대조해 실패 모드를 자동 분류한다. 벡터 DB 없음, SDK 없음(httpx), 임베딩 없음(어휘 검색).
필요해지면 `retrieve.Scorer` 구현체 하나를 추가한다.

```
question ─▶ retrieve(catalog) ─▶ prompt ─▶ LLM ─▶ SQL ─▶ guard(SELECT only) ─▶ DuckDB
                │                                            │ error? → repair ×1
                └── SchemaDoc (table, path, columns, samples) └──────────────▶ Trace
```

## 사용

```bash
pip install de-augur
export ANTHROPIC_API_KEY=...            # 또는 OPENAI_API_KEY + --provider openai

augur index eval/sources.yaml            # {tables: {orders: './data/orders/*.parquet'}} → catalog.json
augur ask "How many orders are paid?"    # SQL은 stderr, 결과는 stdout(jsonl)
augur eval eval/cases.yaml               # 실패 모드별 집계 + Parquet/JSONL 기록
augur report                             # run 간 추이
gladius query "SELECT failure_mode, count(*) FROM './data/augur/eval/*.parquet' GROUP BY 1"
```

## 실패 모드 (자동 분류, 단계 순)

| 모드 | 판정 | 원인 층 |
|---|---|---|
| RETRIEVAL_MISS | 케이스의 `tables`가 검색 결과에 없음 | retrieve |
| NON_SELECT | 쓰기 문장 — 실행 전 차단 | prompt/model |
| UNANSWERABLE | 모델이 답 불가 선언(정답은 answerable) | prompt/model |
| HALLUCINATED_TABLE | 카탈로그에 없는 경로 참조 | prompt/model |
| HALLUCINATED_COLUMN | DuckDB "not found in FROM clause" | schema render/model |
| EXEC_ERROR | 그 외 실행 실패(문법 등) | model |
| WRONG_SHAPE | 실행됐지만 컬럼 수/행 수가 골든과 다름 | semantic (집계 축·필터) |
| WRONG_RESULT | 모양은 같은데 값이 다름 | semantic (조건·리터럴) |
| CORRECT | 집합 비교 일치 | — |

앞 단계 실패가 뒷 단계 실패를 만든다. 그래서 RETRIEVAL_MISS가 결과 일치보다 우선한다 —
"우연히 맞은 것"은 개선 신호가 아니다.

## 케이스 파일

```yaml
cases:
  - id: l3-001
    question: Total paid revenue per product name.
    golden_sql: SELECT ... JOIN ...
    tables: [orders, products]     # 검색이 반드시 맞혀야 하는 테이블
    answerable: true               # false면 UNANSWERABLE이 정답
    notes: 자유 텍스트 (수동 라벨, 함정 설명)
```

동봉 `eval/cases.yaml` 34건 = L1 단일 집계 · L2 필터/그룹 · L3 JOIN · L4 시간 · L5 함정
(답 불가, 없는 리터럴, DML 유도, 한국어). `eval/make_fixture.py`가 seed 고정 합성 데이터를 만든다.

## 하지 않는 것

- ORDER BY 의미 검증(집합 비교만). 순서가 답인 케이스는 `notes`에 표시.
- 스키마 수백 개 규모의 검색 품질 보장 — 어휘 검색의 한계이며 측정 대상.
- 자동 재시도 2회 이상. 1회 repair로 못 고치면 실패로 센다.
