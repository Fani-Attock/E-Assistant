import argparse
import random
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient
from tqdm import tqdm

from src.core.logging_utils import setup_logging
from src.core.settings import Settings


EVENT_WEIGHTS = {
    "view": 1.0,
    "click": 2.0,
    "save": 3.0,
    "purchase": 5.0,
}


logger = setup_logging("jobs.bootstrap_interactions")


def _event_type(rand: random.Random) -> str:
    x = rand.random()
    if x < 0.70:
        return "view"
    if x < 0.90:
        return "click"
    if x < 0.98:
        return "save"
    return "purchase"


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap synthetic user interactions for CF cold start.")
    parser.add_argument("--users", type=int, default=200, help="Number of synthetic users")
    parser.add_argument("--events-per-user", type=int, default=20, help="Interactions per synthetic user")
    parser.add_argument("--lookback-days", type=int, default=30, help="Spread events across past N days")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--clear-existing", action="store_true", help="Delete existing interactions before bootstrapping")
    args = parser.parse_args()

    settings = Settings()
    db = MongoClient(settings.mongo_uri)[settings.app_db_name]
    offers_col = db[settings.normalized_collection]
    interactions_col = db[settings.interactions_collection]

    offers = list(
        offers_col.find(
            {"in_stock": True, "price_pkr": {"$ne": None}, "offer_id": {"$ne": None}},
            {"_id": 0, "offer_id": 1, "source": 1, "link": 1, "brand": 1, "price_pkr": 1},
        )
    )
    if not offers:
        raise RuntimeError("No normalized offers available to bootstrap interactions.")

    if args.clear_existing:
        deleted = interactions_col.delete_many({}).deleted_count
        logger.info("Cleared existing interactions: deleted=%s", deleted)

    rand = random.Random(args.seed)
    by_brand: dict[str, list[dict]] = {}
    global_pool: list[dict] = []
    for o in offers:
        global_pool.append(o)
        brand = (o.get("brand") or "").strip().lower()
        if brand:
            by_brand.setdefault(brand, []).append(o)

    now = datetime.now(timezone.utc)
    docs: list[dict] = []
    total_events = args.users * args.events_per_user
    progress = tqdm(total=total_events, desc="bootstrap_interactions", unit="event")

    for idx in range(args.users):
        user_id = f"synthetic_user_{idx:05d}"
        preferred_brand = rand.choice(list(by_brand.keys())) if by_brand else ""
        brand_pool = by_brand.get(preferred_brand, [])
        for _ in range(args.events_per_user):
            # Most events follow a user preference bucket; others are exploratory.
            if brand_pool and rand.random() < 0.75:
                offer = rand.choice(brand_pool)
            else:
                offer = rand.choice(global_pool)

            event_type = _event_type(rand)
            age_seconds = rand.randint(0, max(args.lookback_days, 1) * 24 * 3600)
            docs.append(
                {
                    "user_id": user_id,
                    "offer_id": offer["offer_id"],
                    "event_type": event_type,
                    "weight": EVENT_WEIGHTS[event_type],
                    "is_synthetic": True,
                    "source": offer.get("source"),
                    "link": offer.get("link"),
                    "event_ts": now - timedelta(seconds=age_seconds),
                }
            )
            if len(docs) >= 5000:
                interactions_col.insert_many(docs, ordered=False)
                docs.clear()
            progress.update(1)

    if docs:
        interactions_col.insert_many(docs, ordered=False)
    progress.close()

    total = interactions_col.count_documents({})
    logger.info("Bootstrap complete: users=%s events_per_user=%s total_interactions=%s", args.users, args.events_per_user, total)


if __name__ == "__main__":
    main()
