"""Arsenal — 우산 CLI. 단일 진입점, 새 로직 없음 (pugio·gladius 위임).

원클릭 경험의 실체: init(레시피 스캐폴드) → run(수집→변환 일괄) → query(즉석 확인).
구현: docs/plans/2026-07-08-m4-integration-release.md Task 4.0~4.1
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("de-arsenal")
except PackageNotFoundError:
    __version__ = "0.3.0"
