from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.core.normalize import parse_last_scraped


def to_utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return parse_last_scraped(value)


def operational_freshness_dt(row: dict[str, Any]) -> datetime | None:
    # Pipeline freshness marker: last_seen_at is updated even when payload is unchanged.
    seen = to_utc_datetime(row.get("last_seen_at"))
    if seen is not None:
        return seen
    # Fallbacks for legacy documents.
    scraped_dt = to_utc_datetime(row.get("last_scraped_dt"))
    if scraped_dt is not None:
        return scraped_dt
    return to_utc_datetime(row.get("last_scraped"))


def stale_offer_query(cutoff: datetime) -> dict[str, Any]:
    cutoff_utc = cutoff.astimezone(timezone.utc) if cutoff.tzinfo else cutoff.replace(tzinfo=timezone.utc)
    return {
        "$or": [
            {"last_seen_at": {"$lt": cutoff_utc}},
            {
                "$and": [
                    {"last_seen_at": {"$exists": False}},
                    {"last_scraped_dt": {"$lt": cutoff_utc}},
                ]
            },
            {
                "$and": [
                    {"last_seen_at": {"$exists": False}},
                    {"last_scraped_dt": None},
                ]
            },
        ]
    }
