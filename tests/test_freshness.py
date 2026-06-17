from datetime import datetime, timedelta, timezone

from src.core.freshness import operational_freshness_dt, stale_offer_query
from src.core.normalize import parse_last_scraped


def test_parse_last_scraped_supports_iso_with_tz():
    out = parse_last_scraped("2026-03-05T08:45:48.804000+00:00")
    assert out is not None
    assert out.tzinfo is not None
    assert out.year == 2026
    assert out.month == 3


def test_operational_freshness_prefers_last_seen_at():
    row = {
        "last_seen_at": datetime(2026, 3, 5, 10, 0, 0, tzinfo=timezone.utc),
        "last_scraped_dt": datetime(2026, 3, 3, 10, 0, 0, tzinfo=timezone.utc),
    }
    out = operational_freshness_dt(row)
    assert out == row["last_seen_at"]


def test_stale_offer_query_targets_last_seen_primary():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    query = stale_offer_query(cutoff)
    assert "$or" in query
    assert isinstance(query["$or"], list)
    assert any("last_seen_at" in block for block in query["$or"] if isinstance(block, dict))
