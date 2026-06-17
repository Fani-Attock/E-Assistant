import argparse
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pymongo import MongoClient, UpdateOne
from tqdm import tqdm

from src.core.logging_utils import setup_logging
from src.core.normalize import normalize_image_gallery, offer_id_from_source_link
from src.core.normalize import primary_image_from_gallery, stable_fingerprint
from src.core.settings import Settings

logger = setup_logging("jobs.ingest_sources")


def load_sources_config(config_path: Path) -> list[dict]:
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    sources = cfg.get("sources", [])
    return [s for s in sources if s.get("enabled", False)]


def ingest_source(client: MongoClient, settings: Settings, source_cfg: dict, batch_size: int = 1000) -> tuple[int, int, int]:
    src_db = client[source_cfg["source_db"]]
    src_col = src_db[source_cfg["source_collection"]]
    dst_col = client[settings.app_db_name][settings.raw_collection]

    inserted = 0
    updated = 0
    unchanged = 0
    processed = 0
    ops: list[UpdateOne] = []
    touch_ops: list[UpdateOne] = []
    source_name = source_cfg["name"]
    existing = {
        (d.get("source"), d.get("link")): d.get("raw_fingerprint")
        for d in dst_col.find(
            {"source_name": source_name},
            {"_id": 0, "source": 1, "link": 1, "raw_fingerprint": 1},
        )
    }

    total = src_col.estimated_document_count()
    cursor = src_col.find({})
    progress = tqdm(cursor, total=total if total > 0 else None, desc=f"ingest:{source_name}", unit="doc")
    for doc in progress:
        title = str(doc.get("title", "")).strip()
        link = str(doc.get("link", "")).strip()
        if not title or not link:
            continue

        source = str(doc.get("source", source_name)).strip().lower()
        offer_id = offer_id_from_source_link(source, link)
        now = datetime.now(timezone.utc)
        processed += 1
        payload = {
            "offer_id": offer_id,
            "source": source,
            "source_name": source_name,
            "site": source_cfg.get("site"),
            "title": title,
            "link": link,
            "category": doc.get("category"),
            "subcategory": doc.get("subcategory"),
            "raw_price": doc.get("price"),
            "rating": doc.get("rating"),
            "review_count": doc.get("review_count"),
            "images": normalize_image_gallery(doc.get("images") or doc.get("image")),
            "image": primary_image_from_gallery(doc.get("images") or doc.get("image")),
            "specifications": doc.get("specifications"),
            "last_scraped": doc.get("last_scraped"),
            "raw_doc": {k: v for k, v in doc.items() if k != "_id"},
            "ingested_at": now,
            "last_seen_at": now,
        }
        raw_fp = stable_fingerprint(
            {
                "title": title,
                "link": link,
                "category": doc.get("category"),
                "subcategory": doc.get("subcategory"),
                "raw_price": doc.get("price"),
                "rating": doc.get("rating"),
                "review_count": doc.get("review_count"),
                "images": payload["images"],
                "image": payload["image"],
                "specifications": doc.get("specifications"),
                "last_scraped": doc.get("last_scraped"),
            }
        )
        payload["raw_fingerprint"] = raw_fp
        key = (source, link)
        prev_fp = existing.get(key)
        if prev_fp == raw_fp:
            unchanged += 1
            touch_ops.append(
                UpdateOne(
                    {"source": source, "link": link},
                    {"$set": {"last_seen_at": now}},
                    upsert=False,
                )
            )
        else:
            ops.append(
                UpdateOne(
                    {"source": source, "link": link},
                    {"$set": payload, "$setOnInsert": {"created_at": now}},
                    upsert=True,
                )
            )
            existing[key] = raw_fp
        if len(ops) >= batch_size:
            result = dst_col.bulk_write(ops, ordered=False)
            inserted += result.upserted_count
            updated += result.modified_count
            ops.clear()
        if len(touch_ops) >= batch_size:
            dst_col.bulk_write(touch_ops, ordered=False)
            touch_ops.clear()
        if processed % 50 == 0:
            progress.set_postfix(inserted=inserted, updated=updated, unchanged=unchanged)

    if ops:
        result = dst_col.bulk_write(ops, ordered=False)
        inserted += result.upserted_count
        updated += result.modified_count
    if touch_ops:
        dst_col.bulk_write(touch_ops, ordered=False)
    progress.set_postfix(inserted=inserted, updated=updated, unchanged=unchanged)
    progress.close()

    return inserted, updated, unchanged


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest source collections into offers_raw.")
    parser.add_argument("--config", default="config/sources.yaml")
    args = parser.parse_args()

    settings = Settings()
    client = MongoClient(settings.mongo_uri)
    sources = load_sources_config(Path(args.config))
    logger.info("Starting ingestion: sources=%s", len(sources))

    total_ins = 0
    total_upd = 0
    total_unch = 0
    for src in sources:
        logger.info("Ingesting source=%s db=%s collection=%s", src["name"], src["source_db"], src["source_collection"])
        ins, upd, unch = ingest_source(client, settings, src)
        total_ins += ins
        total_upd += upd
        total_unch += unch
        logger.info("Source complete: source=%s inserted=%s updated=%s unchanged=%s", src["name"], ins, upd, unch)

    logger.info(
        "Ingestion finished: total_inserted=%s total_updated=%s total_unchanged=%s",
        total_ins,
        total_upd,
        total_unch,
    )


if __name__ == "__main__":
    main()
