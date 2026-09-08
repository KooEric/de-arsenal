"""검색 — 질문에 관련 있는 테이블만 프롬프트에 넣는다.

왜 전부 안 넣는가: 테이블이 수십 개면 컨텍스트가 커지고, 무관한 테이블이 오답
JOIN을 유도한다(WRONG_TABLE 실패 모드). 왜 어휘 매칭인가: 테이블·컬럼명은 짧은
식별자라 임베딩 이득이 작고, 결정적이며 의존성이 없다. 한계는 docs/08-limits.md에
적는다. 임베딩이 필요해지면 Scorer 프로토콜 구현체를 하나 추가한다.
"""

import re
import typing as t

from augur.catalog import Catalog, SchemaDoc

DEFAULT_TOP_K = 3
TOKEN_RE = re.compile(r"[a-z0-9가-힣]+")
TABLE_NAME_WEIGHT = 3.0
COLUMN_NAME_WEIGHT = 2.0
SAMPLE_VALUE_WEIGHT = 1.0


def tokenize(text: str) -> frozenset[str]:
    """소문자 + snake_case 분리. 'order_id' → {'order', 'id', 'order_id'}."""
    lowered = text.lower()
    parts = set(TOKEN_RE.findall(lowered))
    for p in list(parts):
        if "_" in p:
            parts.update(p.split("_"))
    parts.update(w for w in re.split(r"[^a-z0-9가-힣_]+", lowered) if w)
    return frozenset(parts)


class Scorer(t.Protocol):
    def score(self, question: str, doc: SchemaDoc) -> float: ...


class LexicalScorer:
    """토큰 겹침 가중합. 복수형('orders' vs 'order')은 접미사 s 제거로 흡수."""

    @staticmethod
    def _stem(tok: str) -> str:
        return tok[:-1] if tok.endswith("s") and len(tok) > 3 else tok

    def score(self, question: str, doc: SchemaDoc) -> float:
        q = {self._stem(x) for x in tokenize(question)}
        total = 0.0
        for tok in tokenize(doc.table):
            if self._stem(tok) in q:
                total += TABLE_NAME_WEIGHT
        for col in doc.columns:
            if any(self._stem(tok) in q for tok in tokenize(col.name)):
                total += COLUMN_NAME_WEIGHT
            if any(self._stem(s.lower()) in q for s in col.samples):
                total += SAMPLE_VALUE_WEIGHT
        return total


def retrieve(
    question: str, catalog: Catalog, *, top_k: int = DEFAULT_TOP_K, scorer: Scorer | None = None
) -> tuple[SchemaDoc, ...]:
    """점수 내림차순 top_k. 점수 0인 테이블은 제외 — 단, 전부 0이면 전체를 돌려준다
    (검색이 아무것도 못 찾았을 때 빈 프롬프트보다 낫다; 이 경우는 trace에 남는다)."""
    s = scorer or LexicalScorer()
    scored = sorted(
        ((s.score(question, d), d) for d in catalog.tables),
        key=lambda x: (-x[0], x[1].table),
    )
    hits = tuple(d for score, d in scored if score > 0)[:top_k]
    if hits:
        return hits
    return tuple(d for _, d in scored)[:top_k]
