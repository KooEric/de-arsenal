from datetime import UTC, datetime, timedelta

import pytest
from scorpio import assess_freshness

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def test_freshness_is_fresh_within_limit() -> None:
    result = assess_freshness(
        (NOW - timedelta(seconds=30)).isoformat(), max_age_seconds=60, now=NOW
    )
    assert result.status == "fresh"
    assert result.alert is False


def test_freshness_emits_stale_alert() -> None:
    result = assess_freshness(
        (NOW - timedelta(seconds=61)).isoformat(), max_age_seconds=60, now=NOW
    )
    assert result.status == "stale"
    assert result.alert is True
    assert "61.0s" in result.message


def test_freshness_handles_unknown_and_invalid_input() -> None:
    assert assess_freshness(None, max_age_seconds=60, now=NOW).status == "unknown"
    with pytest.raises(ValueError, match="invalid completion"):
        assess_freshness("not-a-timestamp", max_age_seconds=60, now=NOW)
    with pytest.raises(ValueError, match="positive"):
        assess_freshness(None, max_age_seconds=0, now=NOW)
