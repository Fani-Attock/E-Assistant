import argparse
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient

from src.core.freshness import stale_offer_query
from src.core.logging_utils import setup_logging
from src.core.settings import Settings

logger = setup_logging("jobs.cleanup_stale_offers")


def main() -> None:
    parser = argparse.ArgumentParser(description="Mark or delete stale offers.")
    parser.add_argument("--stale-hours", type=int, default=72)
    parser.add_argument("--delete", action="store_true", help="Delete stale offers instead of marking inactive")
    args = parser.parse_args()

    settings = Settings()
    db = MongoClient(settings.mongo_uri)[settings.app_db_name]
    col = db[settings.normalized_collection]

    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.stale_hours)
    query = stale_offer_query(cutoff)

    if args.delete:
        result = col.delete_many(query)
        logger.info("Deleted stale offers: count=%s cutoff=%s", result.deleted_count, cutoff.isoformat())
    else:
        result = col.update_many(
            query,
            {"$set": {"in_stock": False, "is_stale": True, "stale_marked_at": datetime.now(timezone.utc)}},
        )
        logger.info("Marked stale offers: matched=%s modified=%s cutoff=%s", result.matched_count, result.modified_count, cutoff.isoformat())


if __name__ == "__main__":
    main()
