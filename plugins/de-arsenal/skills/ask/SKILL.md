---
name: ask
description: Answer a question about collected data in ordinary words by writing and running DuckDB SQL over a DE Arsenal project's output. Use for "how many", "which", "top 10", "compare", "trend", "what changed", exporting an answer to CSV, or "지난달 매출 얼마야", "거래처별로 합계 보여줘", "제일 많이 팔린 게 뭐야", "이 데이터 요약해줘". No SQL knowledge is expected of the person.
---

# Ask

The person asks in words and gets an answer in words, with the number, how it
was computed, and what it does not include. You write the SQL; they do not
need to see it unless they ask.

## 1. Know the table before answering

```bash
arsenal query "DESCRIBE SELECT * FROM './data/clean/*.parquet'"
arsenal query "SUMMARIZE SELECT * FROM './data/clean/*.parquet'"
arsenal query "SELECT * FROM './data/clean/*.parquet' LIMIT 5"
```

`SUMMARIZE` gives each column's min, max, distinct count and share of NULLs.
Read it for the things that change an answer: the date range actually present,
NULLs in the column being summed, a status column with a "cancelled" value.

Where the data is:

| Output | Query it as |
|---|---|
| Parquet files | `'./data/clean/*.parquet'` |
| DuckDB sink | `ATTACH 'warehouse.duckdb' AS w (READ_ONLY); SELECT ... FROM w.orders` |
| Postgres sink | The database's own client; `arsenal query` does not connect to it |

Prefer the cleaned output over the raw collection when both exist.

## 2. Pin down the question

Words are looser than data. Settle the ambiguity that would change the number,
by looking first and asking only if the data cannot decide:

- **Which period?** "Last month" against today's date, in which timezone.
- **Which rows count?** Cancelled, refunded, test, internal.
- **Which measure?** "Sales" as order count, gross amount, or net.
- **Per what?** One row per order, per line item, per customer.

If there is one reasonable reading, use it and say which one you used. If two
readings give different answers, give both numbers rather than guessing.

## 3. Run it

```bash
arsenal query "
  SELECT customer, count(*) AS orders, sum(amount) AS revenue
  FROM './data/clean/*.parquet'
  WHERE order_date >= DATE '2026-10-01' AND order_date < DATE '2026-11-01'
  GROUP BY ALL ORDER BY revenue DESC LIMIT 10"
```

Useful shapes:

| Asked | SQL shape |
|---|---|
| Total, average, how many | `count(*)`, `sum(x)`, `avg(x)`, `count(DISTINCT x)` |
| By category | `GROUP BY ALL ORDER BY ... DESC` |
| Over time | `date_trunc('month', d)`, then `GROUP BY ALL ORDER BY 1` |
| Top N | `ORDER BY x DESC LIMIT n` |
| Share of total | `x / sum(x) OVER ()` |
| Change from the previous period | `x - lag(x) OVER (ORDER BY period)` |
| In one set but not the other | `LEFT JOIN ... WHERE other.key IS NULL` |
| Looks like a duplicate | `GROUP BY key HAVING count(*) > 1` |

## 4. Check the answer before saying it

- Does the total equal the sum of its parts? Run the ungrouped total too.
- How many rows were excluded by the filter, and how many had NULL in the
  measured column? `sum` skips NULLs without saying so.
- Does the result cover the whole period asked for, or does the data stop
  earlier?
- Is one value the person knows by heart right?

## 5. Answer

Lead with the answer. Then the basis in one line. Then the caveat, if any.

> 10월 매출은 **3억 4,120만 원**입니다 (주문 1,284건).
> 취소된 주문 41건은 뺐고, 주문일 기준입니다.
> 금액이 비어 있는 주문이 6건 있어 합계에 들어가지 않았습니다.

A small table when the answer is a list. The SQL only if they ask how.

To hand over a file:

```bash
arsenal query "SELECT ..." --format csv > answer.csv      # table | csv | jsonl
```

## When the question keeps coming back

A question asked every week should become a saved transform so the answer is a
file that refreshes with the pipeline: write it as a `sql:` step (see
`transform`) and add it to `arsenal.yaml`.

## Limits

- Answers come from what was collected. Say when the data is older than the
  question: `pugio status collect.yaml --cost` shows the last completion time.
- If the needed column does not exist, say so. Do not approximate it from
  another column without saying that is what you did.
