import argparse
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import MongoClient

from src.core.settings import Settings


def merge_query(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    if not base:
        return dict(extra)
    return {"$and": [base, extra]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Report interaction counts (real vs synthetic) for CF readiness.")
    parser.add_argument("--hours", type=int, default=0, help="Optional lookback window in hours (0=all time).")
    args = parser.parse_args()

    settings = Settings()
    col = MongoClient(settings.mongo_uri)[settings.app_db_name][settings.interactions_collection]

    base_query: dict[str, Any] = {}
    if args.hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)
        base_query["event_ts"] = {"$gte": cutoff}

    real_filter = {"$or": [{"is_synthetic": {"$exists": False}}, {"is_synthetic": False}]}
    synthetic_filter = {"is_synthetic": True}

    total = col.count_documents(base_query)
    real_total = col.count_documents(merge_query(base_query, real_filter))
    synthetic_total = col.count_documents(merge_query(base_query, synthetic_filter))
    real_users = len(col.distinct("user_id", merge_query(base_query, real_filter)))
    synthetic_users = len(col.distinct("user_id", merge_query(base_query, synthetic_filter)))

    pipeline = []
    if base_query:
        pipeline.append({"$match": base_query})
    pipeline.extend(
        [
            {
                "$group": {
                    "_id": {
                        "event_type": "$event_type",
                        "is_synthetic": {"$ifNull": ["$is_synthetic", False]},
                    },
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"_id.event_type": 1, "_id.is_synthetic": 1}},
        ]
    )
    by_event = list(col.aggregate(pipeline))

    summary = {
        "window_hours": args.hours,
        "total": total,
        "real_total": real_total,
        "synthetic_total": synthetic_total,
        "real_users": real_users,
        "synthetic_users": synthetic_users,
        "event_breakdown": [
            {
                "event_type": item["_id"]["event_type"],
                "is_synthetic": bool(item["_id"]["is_synthetic"]),
                "count": int(item["count"]),
            }
            for item in by_event
        ],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
