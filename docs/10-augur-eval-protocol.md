# 10. Augur eval 프로토콜 — 실패 분석 글을 쓰기 위한 절차

목표: "만들고, 깨뜨리고, 분류하고, 고치고, 다시 잰다"를 수치로 남긴다. 결과물은
영문 분석 글 1편(`docs/augur-analysis-template.md`) + Parquet 런 기록.

## 1. 베이스라인 (Day 1)

```bash
python packages/augur/eval/make_fixture.py ./data
augur index packages/augur/eval/sources.yaml
augur eval packages/augur/eval/cases.yaml --provider anthropic --run-id base-anthropic
augur eval packages/augur/eval/cases.yaml --provider openai    --run-id base-openai
augur eval packages/augur/eval/cases.yaml --provider anthropic --top-k 1 --run-id base-topk1
```

세 run으로 얻는 것: 모델 간 차이, 검색 폭(top_k)이 RETRIEVAL_MISS와 WRONG_TABLE에 미치는 영향.

## 2. 케이스 50건 채우기 (Day 1~3)

34건 → 50건. 추가는 **실패한 곳 근처**에서 한다. 베이스라인에서 WRONG_RESULT가 L4에
몰리면 L4를 늘린다. 각 케이스에 `notes`로 "무엇을 검증하는가"를 한 줄.

## 3. 실패 읽기 (Day 3~5)

```sql
-- gladius query 로
SELECT failure_mode, count(*) FROM './data/augur/eval/base-anthropic.parquet' GROUP BY 1;
SELECT case_id, generated_sql, error FROM './data/augur/eval/base-anthropic.parquet'
WHERE failure_mode <> 'CORRECT';
```

trace JSONL(`data/augur/eval/traces/<run_id>.jsonl`의 `raw_completion`, `repair_raw`)을 열어 **모델이 실제로 뭐라고 했는지** 본다.
자동 분류가 틀린 케이스는 `notes`에 수동 라벨을 적고 분류기(`evaluate.classify`)를 고친다 —
분류기 오류도 실패 모드다.

## 4. 개입 1개씩 (Day 5~10)

한 번에 하나만 바꾸고 run_id를 새로 준다. 후보:

| 개입 | 겨냥 모드 | 코드 위치 |
|---|---|---|
| 샘플값 개수 5→10 | HALLUCINATED literal → WRONG_RESULT | catalog.MAX_SAMPLE_VALUES |
| 한국어 동의어 사전 | RETRIEVAL_MISS(한국어) | retrieve.LexicalScorer |
| few-shot 2개 추가 | EXEC_ERROR, WRONG_SHAPE | generate.SYSTEM_PROMPT |
| 컬럼 설명 필드 | HALLUCINATED_COLUMN | catalog.ColumnDoc |
| repair 0회 vs 1회 | 전체 (repair가 실제로 구하는 비율) | generate.MAX_REPAIR_ATTEMPTS |

`augur report`가 run 간 표를 낸다. **오른 것과 함께 내린 것도 적는다.**

## 5. 글 (Day 10~14)

템플릿대로. 숫자 없는 문장은 뺀다.
