from augur.catalog import Catalog
from augur.retrieve import retrieve, tokenize


def test_tokenize_splits_snake_case() -> None:
    assert {"order", "id", "order_id"} <= tokenize("order_id")


def test_retrieve_prefers_matching_table(catalog: Catalog) -> None:
    hits = retrieve("How many orders are paid?", catalog, top_k=1)
    assert [h.table for h in hits] == ["orders"]


def test_retrieve_sample_value_pulls_table(catalog: Catalog) -> None:
    hits = retrieve("count rows with status refunded", catalog, top_k=1)
    assert hits[0].table == "orders"


def test_retrieve_join_question_returns_both(catalog: Catalog) -> None:
    hits = retrieve("Total paid revenue per product name", catalog, top_k=2)
    assert {h.table for h in hits} == {"orders", "products"}


def test_retrieve_falls_back_to_all_when_no_overlap(catalog: Catalog) -> None:
    hits = retrieve("도시별 고객 수", catalog, top_k=3)
    assert len(hits) == 3  # 한국어 질문: 어휘 겹침 0 → 전체 반환 (한계를 숨기지 않는다)
