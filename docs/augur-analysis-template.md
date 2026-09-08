# Where a schema-RAG text-to-SQL pipeline fails, and what actually fixed it

<!-- Target: 1,200–1,800 words, English. Every claim carries a number from a run_id. -->

## Setup (150 words)
- Data: 3 tables, 400 orders, synthetic, seed-fixed. Why synthetic: goldens must be stable.
- Pipeline: lexical schema retrieval (top_k=3) → LLM (temperature 0) → SELECT-only guard → DuckDB → 1 repair.
- Eval: 50 cases, 5 difficulty levels, result-set comparison, automatic failure-mode classifier.
- Models: <A>, <B>. Cost per full run: <n> input / <n> output tokens.

## Baseline (table)
| mode | model A | model B | top_k=1 |
|---|---|---|---|
| CORRECT | | | |
| RETRIEVAL_MISS | | | |
| ... | | | |

One paragraph: which mode dominates, and whether it is a retrieval or a generation problem.

## Failure 1: <most common mode> (300 words)
- 2 concrete cases: question, generated SQL, golden SQL, what the model "believed".
- Root cause in one sentence.
- Intervention (single change, code pointer) → before/after numbers.
- What got worse.

## Failure 2: <second mode> (300 words)
Same structure.

## Failure 3: the classifier was wrong (200 words)
Cases where automatic classification mislabeled. Fix to `classify`. Why eval code needs eval.

## What I would not do (150 words)
- Vector retrieval: at 3–30 tables, lexical + samples reached <n>% retrieval hit. Not worth the dependency yet.
- More repair attempts: repair rescued <n>/<m> failures; the rest were semantic, not syntactic.

## Numbers I still don't trust (100 words)
Non-determinism observed, tie-order cases, Korean-question retrieval — with counts.

## Repro
`git clone ... && uv sync && python packages/augur/eval/make_fixture.py && augur eval ...`
