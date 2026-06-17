import argparse
from datetime import datetime, timezone

from pymongo import MongoClient, UpdateOne
from tqdm import tqdm

from src.core.logging_utils import setup_logging
from src.core.normalize import (
    canonical_key,
    detect_stock,
    extract_brand,
    extract_model,
    extract_ram_gb,
    extract_storage_gb,
    normalize_price_to_pkr,
    normalize_image_gallery,
    normalize_text,
    parse_last_scraped,
    parse_rating,
    parse_review_count,
    primary_image_from_gallery,
    stable_fingerprint,
)
from src.core.settings import Settings

logger = setup_logging("jobs.normalize_offers")


def normalize_one(raw: dict) -> dict | None:
    title = str(raw.get("title", "")).strip()
    link = str(raw.get("link", "")).strip()
    source = str(raw.get("source", "")).strip().lower()
    if not title or not link or not source:
        return None

    price_text = raw.get("raw_price")
    price_pkr = normalize_price_to_pkr(price_text)
    stock = detect_stock(str(price_text or ""), title)
    brand = extract_brand(title)
    storage = extract_storage_gb(title)
    ram = extract_ram_gb(title)
    model = extract_model(title, brand)
    canonical = canonical_key(brand, model, storage, ram)
    last_scraped_dt = parse_last_scraped(raw.get("last_scraped"))
    raw_doc = raw.get("raw_doc", {}) or {}
    rating = parse_rating(
        raw.get("rating")
        or raw_doc.get("rating")
        or raw_doc.get("stars")
        or raw_doc.get("average_rating")
        or raw_doc.get("avg_rating")
    )
    review_count = parse_review_count(
        raw.get("review_count")
        or raw_doc.get("review_count")
        or raw_doc.get("ratings_count")
        or raw_doc.get("reviews")
    )

    now = datetime.now(timezone.utc)
    images = normalize_image_gallery(raw.get("images") or raw.get("image"))
    primary_image = primary_image_from_gallery(images or raw.get("image"))
    return {
        "offer_id": raw.get("offer_id"),
        "canonical_key": canonical,
        "source": source,
        "source_name": raw.get("source_name"),
        "site": raw.get("site"),
        "title": title,
        "title_normalized": normalize_text(title),
        "link": link,
        "category": raw.get("category"),
        "subcategory": raw.get("subcategory"),
        "brand": brand,
        "model": model,
        "storage_gb": storage,
        "ram_gb": ram,
        "price_pkr": price_pkr,
        "shipping_pkr": 0.0,
        "in_stock": stock,
        "rating": rating,
        "review_count": review_count,
        "images": images,
        "image": primary_image,
        "specifications": raw.get("specifications"),
        "last_scraped": raw.get("last_scraped"),
        "last_scraped_dt": last_scraped_dt,
        "last_seen_at": now,
        "normalized_at": now,
        "content_fingerprint": stable_fingerprint(
            {
                "title": normalize_text(title),
                "brand": brand,
                "model": model,
                "storage_gb": storage,
                "ram_gb": ram,
                "price_pkr": price_pkr,
                "in_stock": stock,
                "rating": rating,
                "review_count": review_count,
                "images": images,
                "image": primary_image,
                "specifications": raw.get("specifications"),
            }
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize offers_raw into offers_normalized.")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of raw records (0=all)")
    args = parser.parse_args()

    settings = Settings()
    client = MongoClient(settings.mongo_uri)
    db = client[settings.app_db_name]
    raw_col = db[settings.raw_collection]
    norm_col = db[settings.normalized_collection]
    history_col = db[settings.price_history_collection]

    cursor = raw_col.find({})
    total = raw_col.estimated_document_count()
    if args.limit > 0:
        cursor = cursor.limit(args.limit)
        total = min(total, args.limit) if total else args.limit
    logger.info("Starting normalization: total=%s limit=%s", total, args.limit)

    existing = {
        (d.get("source"), d.get("link")): d
        for d in norm_col.find(
            {},
            {
                "_id": 0,
                "source": 1,
                "link": 1,
                "content_fingerprint": 1,
                "price_pkr": 1,
                "rating": 1,
                "review_count": 1,
                "in_stock": 1,
            },
        )
    }

    norm_ops: list[UpdateOne] = []
    touch_ops: list[UpdateOne] = []
    hist_ops: list[UpdateOne] = []
    count = 0
    changed = 0
    unchanged = 0
    progress = tqdm(cursor, total=total if total > 0 else None, desc="normalize", unit="doc")
    for raw in progress:
        norm = normalize_one(raw)
        if norm is None:
            continue
        count += 1
        key = (norm["source"], norm["link"])
        prev = existing.get(key)
        is_changed = prev is None or prev.get("content_fingerprint") != norm["content_fingerprint"]

        if is_changed:
            changed += 1
            now = datetime.now(timezone.utc)
            norm_ops.append(
                UpdateOne(
                    {"source": norm["source"], "link": norm["link"]},
                    {"$set": norm, "$setOnInsert": {"created_at": now}},
                    upsert=True,
                )
            )
            observed_at = norm["last_scraped_dt"] or now
            hist_ops.append(
                UpdateOne(
                    {"source": norm["source"], "link": norm["link"], "observed_at": observed_at},
                    {
                        "$set": {
                            "price_pkr": norm["price_pkr"],
                            "rating": norm["rating"],
                            "review_count": norm["review_count"],
                            "in_stock": norm["in_stock"],
                            "source_name": norm["source_name"],
                            "canonical_key": norm["canonical_key"],
                        }
                    },
                    upsert=True,
                )
            )
            existing[key] = {
                "content_fingerprint": norm["content_fingerprint"],
                "price_pkr": norm["price_pkr"],
                "rating": norm["rating"],
                "review_count": norm["review_count"],
                "in_stock": norm["in_stock"],
            }
        else:
            unchanged += 1
            now = datetime.now(timezone.utc)
            touch_ops.append(
                UpdateOne(
                    {"source": norm["source"], "link": norm["link"]},
                    {
                        "$set": {
                            "last_seen_at": now,
                            "last_scraped": norm["last_scraped"],
                            "last_scraped_dt": norm["last_scraped_dt"],
                            "normalized_at": now,
                            # Refresh operational fields to avoid stale-cleanup side effects
                            "in_stock": norm["in_stock"],
                            "is_stale": False,
                        },
                        "$unset": {"stale_marked_at": ""},
                    },
                    upsert=False,
                )
            )

        if len(norm_ops) >= 1000:
            norm_col.bulk_write(norm_ops, ordered=False)
            norm_ops.clear()
        if len(touch_ops) >= 1000:
            norm_col.bulk_write(touch_ops, ordered=False)
            touch_ops.clear()
        if len(hist_ops) >= 1000:
            history_col.bulk_write(hist_ops, ordered=False)
            hist_ops.clear()
        if count % 50 == 0:
            progress.set_postfix(processed=count, changed=changed, unchanged=unchanged)

    if norm_ops:
        norm_col.bulk_write(norm_ops, ordered=False)
    if touch_ops:
        norm_col.bulk_write(touch_ops, ordered=False)
    if hist_ops:
        history_col.bulk_write(hist_ops, ordered=False)

    progress.set_postfix(processed=count, changed=changed, unchanged=unchanged)
    progress.close()
    logger.info(
        "Normalization complete: processed=%s changed=%s unchanged=%s",
        count,
        changed,
        unchanged,
    )


if __name__ == "__main__":
    main()
