import argparse
import json
from pathlib import Path

from pymongo import MongoClient

from src.core.logging_utils import setup_logging
from src.core.model_registry import set_active_model
from src.core.runtime_compat import patch_multiprocess_resource_tracker
from src.core.settings import Settings

logger = setup_logging("jobs.train_matcher")


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
    parser = argparse.ArgumentParser(description="Train sentence-transformer matcher.")
    parser.add_argument("--train-data", default="", help="JSONL train split path")
    parser.add_argument("--val-data", default="", help="Optional JSONL validation split path")
    parser.add_argument("--data", default="", help="Optional JSONL labeled pairs path")
    parser.add_argument("--output", default="artifacts/matching_model")
    parser.add_argument("--limit", type=int, default=20000)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--base-model", default="sentence-transformers/all-MiniLM-L6-v2")
    args = parser.parse_args()

    settings = Settings()
    if args.train_data:
        pairs = load_pairs_from_jsonl(Path(args.train_data), args.limit)
        source = "train_jsonl"
    elif args.data:
        pairs = load_pairs_from_jsonl(Path(args.data), args.limit)
        source = "jsonl"
    else:
        pairs = load_pairs_from_mongo(settings, args.limit)
        source = "mongo"
    logger.info("Loaded training pairs: count=%s source=%s", len(pairs), source)

    eval_pairs: list[dict] | None = None
    if args.val_data:
        eval_pairs = load_pairs_from_jsonl(Path(args.val_data), args.limit)
        logger.info("Loaded explicit validation pairs: count=%s path=%s", len(eval_pairs), args.val_data)
        if not eval_pairs:
            raise RuntimeError("Validation split provided but no rows were loaded.")

    if len(pairs) < 100:
        raise RuntimeError("Need at least 100 labeled pairs to train reliably.")

    Path(args.output).mkdir(parents=True, exist_ok=True)
    from src.ml.matching.train import train_matcher

    train_matcher(
        train_samples=pairs,
        output_dir=args.output,
        base_model=args.base_model,
        epochs=args.epochs,
        batch_size=args.batch_size,
        eval_samples=eval_pairs,
    )
    set_active_model(settings.model_registry_dir, "matcher", args.output)
    logger.info("Training complete: model_path=%s", args.output)


if __name__ == "__main__":
    main()
