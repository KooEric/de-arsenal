"""Pugio — 수집·전송 (ETL 엔진).

커넥터 수 대신 신뢰성 코어로 경쟁한다: 재개·멱등·인증 갱신이 기본값.
구조: sources/ sinks/ auth/ validate/ runner.py cli.py (docs/02-architecture.md)
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pugio")
except PackageNotFoundError:
    __version__ = "0.2.0"
