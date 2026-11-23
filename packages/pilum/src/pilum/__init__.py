"""Pilum — the javelin: send changed records out of the lake, once per change.

Experimental (0.6). A dispatch spec names a Parquet dataset or a query, a record
key and one destination (an HTTP endpoint or a Slack webhook). Each run diffs
the source against a local ledger and sends only new, changed or (in ``mirror``
mode) removed records. A record enters the ledger only after the destination
answers 2xx, so a crash re-sends it under the same idempotency key.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("de-pilum")
except PackageNotFoundError:
    __version__ = "0.6.10"

__all__ = ["__version__"]
