from datetime import datetime, timezone
from time import perf_counter

from pymongo import MongoClient, UpdateOne
from tqdm import tqdm

from src.core.logging_utils import setup_logging
from src.core.runtime_compat import patch_multiprocess_resource_tracker
from src.core.settings import Settings
from src.ml.clustering.cluster_offers import cluster_embeddings
from src.ml.matching.infer import ProductMatcher
from src.ml.ranking.rerank import rank_offer

logger = setup_logging("jobs.recluster_offers")


def main() -> None:
    started = perf_counter()
    patch_multiprocess_resource_tracker()
    settings = Settings()
    client = MongoClient(settings.mongo_uri)
    db = client[settings.app_db_name]
    norm_col = db[settings.normalized_collection]
    canonical_col = db[settings.canonical_collection]

    docs = list(
        norm_col.find(
            {"in_stock": True, "price_pkr": {"$ne": None}},
            {
                "_id": 0,
                "offer_id": 1,
                "title": 1,
                "specifications": 1,
                "canonical_key": 1,
                "source": 1,
                "link": 1,
                "price_pkr": 1,
                "shipping_pkr": 1,
                "rating": 1,
                "review_count": 1,
                "image": 1,
            },
        )
    )
    if not docs:
        logger.warning("No normalized offers found.")
        return
    logger.info("Reclustering offers: docs=%s", len(docs))

    logger.info("Loading matcher model: %s", settings.matcher_model_path)
    model_t0 = perf_counter()
    matcher = ProductMatcher(settings.matcher_model_path)
    logger.info("Matcher loaded in %.2fs", perf_counter() - model_t0)

    texts = [f"{d.get('title', '')} {d.get('specifications', '') or ''}" for d in docs]
    logger.info("Encoding offer text embeddings: count=%s", len(texts))
    enc_t0 = perf_counter()
    emb = matcher.encode(texts)
    logger.info("Embeddings generated: shape=%s elapsed=%.2fs", getattr(emb, "shape", None), perf_counter() - enc_t0)

    cluster_t0 = perf_counter()
    labels = cluster_embeddings(emb)
    logger.info(
        "Clustering complete: clusters=%s elapsed=%.2fs",
        len(set(labels)),
        perf_counter() - cluster_t0,
    )

    by_cluster: dict[int, list[dict]] = {}
    for idx, doc in enumerate(docs):
        by_cluster.setdefault(int(labels[idx]), []).append(doc)

    ops: list[UpdateOne] = []
    now = datetime.now(timezone.utc)
    for cluster_id, offers in tqdm(by_cluster.items(), total=len(by_cluster), desc="clusters", unit="cluster"):
        for o in offers:
            total = float(o.get("price_pkr") or 0.0) + float(o.get("shipping_pkr") or 0.0)
            o["_value_score"] = rank_offer(
                total_price_pkr=total,
                match_score=0.8,
                rating=o.get("rating"),
                review_count=o.get("review_count"),
                in_stock=True,
                source=o.get("source"),
                last_scraped_dt=None,
                now=now,
                prefer_value=True,
            )
        best = max(offers, key=lambda o: o["_value_score"])
        canonical_id = f"cluster-{cluster_id}"
        payload = {
            "canonical_id": canonical_id,
            "offer_count": len(offers),
            "best_offer_link": best["link"],
            "best_offer_source": best["source"],
            "best_offer_price_pkr": float(best.get("price_pkr") or 0.0),
            "best_offer_rating": best.get("rating"),
            "best_offer_image": best.get("image"),
            "representative_title": best.get("title"),
            "offer_ids": [o.get("offer_id") for o in offers if o.get("offer_id")],
            "updated_at": now,
        }
        ops.append(UpdateOne({"canonical_id": canonical_id}, {"$set": payload}, upsert=True))

    if ops:
        canonical_col.bulk_write(ops, ordered=False)

    logger.info("Reclustering complete: clusters_upserted=%s elapsed=%.2fs", len(ops), perf_counter() - started)


if __name__ == "__main__":
    main()
