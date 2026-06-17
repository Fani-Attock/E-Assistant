import argparse
import json
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient

from src.core.freshness import stale_offer_query
from src.core.settings import Settings


def ratio(num: int, den: int) -> float:
    return (num / den) if den else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate data quality report for normalized offers.")
    parser.add_argument("--fresh-hours", type=int, default=48)
    parser.add_argument("--fail-on-threshold", action="store_true")
    parser.add_argument("--max-missing-price-ratio", type=float, default=0.2)
    parser.add_argument("--max-missing-title-ratio", type=float, default=0.01)
    args = parser.parse_args()

    settings = Settings()
    db = MongoClient(settings.mongo_uri)[settings.app_db_name]
    col = db[settings.normalized_collection]
    total = col.estimated_document_count()

    missing_price = col.count_documents({"price_pkr": None})
    missing_title = col.count_documents({"$or": [{"title": None}, {"title": ""}]})
    missing_brand = col.count_documents({"brand": None})
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.fresh_hours)
    stale = col.count_documents(stale_offer_query(cutoff))

    report = {
        "total": total,
        "missing_price": missing_price,
        "missing_price_ratio": ratio(missing_price, total),
        "missing_title": missing_title,
        "missing_title_ratio": ratio(missing_title, total),
        "missing_brand": missing_brand,
        "missing_brand_ratio": ratio(missing_brand, total),
        "stale_offers": stale,
        "stale_ratio": ratio(stale, total),
        "fresh_hours": args.fresh_hours,
    }
    print(json.dumps(report, indent=2))

    if args.fail_on_threshold:
        bad = False
        if report["missing_price_ratio"] > args.max_missing_price_ratio:
            bad = True
        if report["missing_title_ratio"] > args.max_missing_title_ratio:
            bad = True
        if bad:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
