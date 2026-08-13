"""Gladius — 변환·쿼리 (핵심 처리).

map/steps 선언 → SQL 컴파일 → DuckDB 벡터화 실행. 단일 노드 수백 GB.
구조: spec.py compile/ engine.py cli.py (docs/02-architecture.md)
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("de-gladius")
except PackageNotFoundError:
    __version__ = "0.3.0"
