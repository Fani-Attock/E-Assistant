import argparse
from datetime import datetime, timezone
from hashlib import sha1
from itertools import combinations

from pymongo import MongoClient, UpdateOne
from tqdm import tqdm

from src.core.logging_utils import setup_logging
from src.core.settings import Settings

logger = setup_logging("jobs.build_match_candidates")


def pair_id(a_offer_id: str, b_offer_id: str) -> str:
    lo, hi = sorted([a_offer_id, b_offer_id])
    return sha1(f"{lo}|{hi}".encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build candidate pairs for same-product labeling.")
    parser.add_argument("--max-per-brand", type=int, default=200)
    parser.add_argument(
        "--include-same-source-positives",
        action="store_true",
        help="Include same-source pairs only when canonical keys match (positive bootstrap).",
    )
    args = parser.parse_args()

    settings = Settings()
    client = MongoClient(settings.mongo_uri)
    db = client[settings.app_db_name]
    norm_col = db[settings.normalized_collection]
    pair_col = db[settings.match_pairs_collection]

    brands = [b for b in norm_col.distinct("brand") if b]
    logger.info("Generating match candidates: brands=%s max_per_brand=%s", len(brands), args.max_per_brand)
    total_ops = 0
    for brand in tqdm(brands, desc="brands", unit="brand"):
        docs = list(
            norm_col.find(
                {"brand": brand},
                {"_id": 0, "offer_id": 1, "title": 1, "source": 1, "canonical_key": 1},
            ).limit(args.max_per_brand)
        )
        total_pairs = len(docs) * (len(docs) - 1) // 2
        ops: list[UpdateOne] = []
        for a, b in tqdm(
            combinations(docs, 2),
            total=total_pairs if total_pairs > 0 else None,
            desc=f"pairs:{brand}",
            unit="pair",
            leave=False,
        ):
            auto_label = int(a.get("canonical_key") == b.get("canonical_key"))
            same_source = a.get("source") == b.get("source")
            if same_source and not (args.include_same_source_positives and auto_label == 1):
                continue
            a_id = a.get("offer_id")
            b_id = b.get("offer_id")
            if not a_id or not b_id:
                continue
            pid = pair_id(a_id, b_id)
            ops.append(
                UpdateOne(
                    {"pair_id": pid},
                    {
                        "$setOnInsert": {
                            "pair_id": pid,
                            "offer_a_id": a_id,
                            "offer_b_id": b_id,
                            "title_a": a.get("title"),
                            "title_b": b.get("title"),
                            "source_a": a.get("source"),
                            "source_b": b.get("source"),
                            "auto_label": auto_label,
                            "label": None,
                            "created_at": datetime.now(timezone.utc),
                        }
                    },
                    upsert=True,
                )
            )
            if len(ops) >= 1000:
                pair_col.bulk_write(ops, ordered=False)
                total_ops += len(ops)
                ops.clear()
        if ops:
            pair_col.bulk_write(ops, ordered=False)
            total_ops += len(ops)
        logger.info("Brand complete: brand=%s docs=%s candidate_pairs=%s", brand, len(docs), total_pairs)

    logger.info("Candidate generation complete: candidate_pairs_upserted=%s", total_ops)


if __name__ == "__main__":
    main()
