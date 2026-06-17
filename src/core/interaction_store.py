from datetime import datetime, timezone

from pymongo import MongoClient

from src.core.normalize import offer_id_from_source_link, stable_fingerprint
from src.core.settings import Settings


EVENT_WEIGHTS = {
    "view": 1.0,
    "click": 2.0,
    "save": 3.0,
    "purchase": 5.0,
}


def log_interaction(
    *,
    settings: Settings,
    user_id: str,
    event_type: str,
    offer_id: str | None = None,
    link: str | None = None,
    source: str | None = None,
    event_ts: datetime | None = None,
    event_id: str | None = None,
    db=None,
) -> dict:
    if not offer_id:
        if not link or not source:
            raise ValueError("Either offer_id or (link + source) is required.")
        offer_id = offer_id_from_source_link(source.lower(), link)
    if db is None:
        client = MongoClient(settings.mongo_uri)
        db = client[settings.app_db_name]
    source_norm = source.lower() if source else None
    event_ts_utc = event_ts or datetime.now(timezone.utc)
    if event_ts_utc.tzinfo is None:
        event_ts_utc = event_ts_utc.replace(tzinfo=timezone.utc)
    else:
        event_ts_utc = event_ts_utc.astimezone(timezone.utc)
    resolved_event_id = event_id or stable_fingerprint(
        {
            "user_id": user_id,
            "offer_id": offer_id,
            "event_type": event_type,
            "event_ts": event_ts_utc.isoformat(),
            "source": source_norm,
            "link": link,
        }
    )
    doc = {
        "user_id": user_id,
        "offer_id": offer_id,
        "event_type": event_type,
        "weight": EVENT_WEIGHTS.get(event_type, 1.0),
        "is_synthetic": False,
        "source": source_norm,
        "link": link,
        "event_ts": event_ts_utc,
        "event_id": resolved_event_id,
    }
    col = db[settings.interactions_collection]
    result = col.update_one({"event_id": resolved_event_id}, {"$setOnInsert": doc}, upsert=True)
    if result.upserted_id is None:
        existing = col.find_one({"event_id": resolved_event_id}, {"_id": 0})
        if existing:
            return existing
    return doc
