import argparse
import json
from collections import defaultdict
from typing import Any

from pymongo import MongoClient

from src.core.settings import Settings
from src.ml.cf.infer_cf import CFRecommender


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate CF model with hit-rate@k on held-out interactions.")
    parser.add_argument("--model-dir", default="artifacts/cf_model")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--max-users", type=int, default=1000)
    parser.add_argument(
        "--only-real",
        action="store_true",
        help="Evaluate using only real interactions (is_synthetic != true).",
    )
    args = parser.parse_args()

    settings = Settings()
    db = MongoClient(settings.mongo_uri)[settings.app_db_name]
    col = db[settings.interactions_collection]
    rec = CFRecommender(args.model_dir)

    query: dict[str, Any] = {"user_id": {"$ne": None}, "offer_id": {"$ne": None}}
    if args.only_real:
        query["$or"] = [{"is_synthetic": {"$exists": False}}, {"is_synthetic": False}]
    rows = list(col.find(query, {"_id": 0, "user_id": 1, "offer_id": 1, "event_ts": 1}).sort([("event_ts", 1)]))
    by_user: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        by_user[r["user_id"]].append(r["offer_id"])

    users = [u for u, items in by_user.items() if len(items) >= 2][: args.max_users]
    if not users:
        raise RuntimeError("No users with enough interactions for evaluation.")

    hits = 0
    total = 0
    candidate_pool = rec.offers
    for u in users:
        hist = by_user[u]
        target = hist[-1]
        scores = rec.score_user_items(u, candidate_pool)
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[: args.k]
        pred_ids = {oid for oid, _ in ranked}
        hits += int(target in pred_ids)
        total += 1

    out = {"users": total, "k": args.k, "hit_rate_at_k": (hits / total) if total else 0.0, "only_real": args.only_real}
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
