"""Augur — 자연어 → SQL (스키마 RAG) + eval 하네스.

파이프라인: catalog(스키마 색인) → retrieve(관련 테이블 선별) → generate(LLM → SQL)
→ execute(DuckDB) → trace(JSONL). evaluate는 이 트레이스를 골든 SQL과 대조해
실패 모드를 자동 분류한다. 엔진은 빌린다 — LLM은 HTTP로 호출, 검색은 어휘 매칭.
벡터 DB 없음. 필요해지면 retrieve.py의 Scorer 하나만 바꾼다.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("de-augur")
except PackageNotFoundError:
    __version__ = "0.3.0"
