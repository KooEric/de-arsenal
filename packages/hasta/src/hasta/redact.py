"""Keep credentials out of Hasta's error messages.

Error text reaches stderr and the batch ledger, so a source URL's password and
query values, and any Postgres DSN, are masked before they get there.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MASK = "***"


def redact_url(url: str) -> str:
    """``https://u:pw@h/p?token=x`` → ``https://u:***@h/p?token=***``."""

    try:
        parts = urlsplit(url)
    except ValueError:
        return MASK
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, host = netloc.rsplit("@", 1)
        user = userinfo.split(":", 1)[0]
        netloc = f"{user}:{MASK}@{host}" if ":" in userinfo else f"{user}@{host}"
    query = urlencode([(key, MASK) for key, _ in parse_qsl(parts.query, keep_blank_values=True)])
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def redact(message: str, secrets: Iterable[str]) -> str:
    """Replace every non-trivial secret (and its URL password) in ``message``."""

    for secret in secrets:
        if len(secret) < 4:
            continue
        message = message.replace(secret, MASK)
        password = urlsplit(secret).password if "://" in secret else None
        if password and len(password) >= 4:
            message = message.replace(password, MASK)
    return message
