import argparse
import json
from pathlib import Path

from pymongo import MongoClient

from src.core.logging_utils import setup_logging
from src.core.runtime_compat import patch_multiprocess_resource_tracker
from src.core.settings import Settings

logger = setup_logging("jobs.evaluate_matcher")


def load_pairs_from_mongo(settings: Settings, limit: int) -> list[dict]:
    client = MongoClient(settings.mongo_uri)
    col = client[settings.app_db_name][settings.match_pairs_collection]
    cursor = col.find({"label": {"$in": [0, 1]}}, {"_id": 0, "title_a": 1, "title_b": 1, "label": 1}).limit(limit)
    return [{"text_a": d["title_a"], "text_b": d["title_b"], "label": float(d["label"])} for d in cursor]


def load_pairs_from_jsonl(path: Path, limit: int) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("label") is None:
                continue
            rows.append({"text_a": row["text_a"], "text_b": row["text_b"], "label": float(row["label"])})
            if len(rows) >= limit:
                break
    return rows


def main() -> None:
    patch_multiprocess_resource_tracker()
    parser = argparse.ArgumentParser(description="Evaluate sentence-transformer matcher.")
    parser.add_argument("--model", default="artifacts/matching_model")
    parser.add_argument("--data", default="", help="Optional JSONL labeled pairs path")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--threshold", type=float, default=0.7)
    args = parser.parse_args()

    settings = Settings()
    if args.data:
        pairs = load_pairs_from_jsonl(Path(args.data), args.limit)
    else:
        pairs = load_pairs_from_mongo(settings, args.limit)
    logger.info("Loaded evaluation pairs: count=%s source=%s", len(pairs), "jsonl" if args.data else "mongo")

    if not pairs:
        raise RuntimeError("No labeled pairs found for evaluation.")

    from src.ml.matching.evaluate import evaluate_pairs

    metrics = evaluate_pairs(pairs, args.model, threshold=args.threshold)
    logger.info("Evaluation complete")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
