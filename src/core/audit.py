from __future__ import annotations

from datetime import datetime, timezone


def write_audit_log(db, collection_name: str, payload: dict) -> None:
    doc = dict(payload)
    doc["ts"] = datetime.now(timezone.utc)
    db[collection_name].insert_one(doc)
