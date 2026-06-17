import argparse
import json
from pathlib import Path

from pymongo import MongoClient
from tqdm import tqdm

from src.core.model_registry import get_active_model
from src.core.runtime_compat import patch_multiprocess_resource_tracker
from src.core.settings import Settings
from src.ml.matching.infer import ProductMatcher


def is_cross_source(row: dict) -> bool:
    a = str(row.get("source_a") or "").strip().lower()
    b = str(row.get("source_b") or "").strip().lower()
    return bool(a and b and a != b)


def build_queue(settings: Settings, limit_scan: int, top_k: int) -> list[dict]:
    col = MongoClient(settings.mongo_uri)[settings.app_db_name][settings.match_pairs_collection]
    rows = list(
        col.find(
            {"label": None},
            {
                "_id": 0,
                "pair_id": 1,
                "title_a": 1,
                "title_b": 1,
                "source_a": 1,
                "source_b": 1,
                "auto_label": 1,
            },
        ).limit(limit_scan)
    )
    if not rows:
        return []

    matcher_path = get_active_model(settings.model_registry_dir, "matcher", settings.matcher_model_path)
    matcher = ProductMatcher(matcher_path or settings.matcher_model_path)

    texts_a = [str(r.get("title_a") or "") for r in rows]
    texts_b = [str(r.get("title_b") or "") for r in rows]
    emb_a = matcher.encode(texts_a)
    emb_b = matcher.encode(texts_b)
    sims = (emb_a * emb_b).sum(axis=1)

    enriched: list[dict] = []
    for i, row in enumerate(tqdm(rows, desc="review_queue", unit="pair")):
        sim = float(sims[i])
        auto_label = row.get("auto_label")
        cross = is_cross_source(row)
        priority = sim
        if auto_label == 1:
            priority += 0.15
        if cross:
            priority += 0.05
        enriched.append(
            {
                "pair_id": row.get("pair_id"),
                "title_a": row.get("title_a"),
                "title_b": row.get("title_b"),
                "source_a": row.get("source_a"),
                "source_b": row.get("source_b"),
                "auto_label": auto_label,
                "cross_source": cross,
                "similarity": round(sim, 6),
                "priority_score": round(priority, 6),
                "suggested_label": 1 if sim >= 0.70 else 0 if sim <= 0.25 else None,
            }
        )
    enriched.sort(key=lambda x: x["priority_score"], reverse=True)
    return enriched[:top_k]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")


def main() -> None:
    patch_multiprocess_resource_tracker()
    parser = argparse.ArgumentParser(description="Build manual-review queue for likely-positive unlabeled pairs.")
    parser.add_argument("--limit-scan", type=int, default=5000, help="How many unlabeled pairs to scan.")
    parser.add_argument("--top-k", type=int, default=300, help="How many prioritized review rows to output.")
    parser.add_argument("--output", default="artifacts/labeling/manual_positive_review_queue.jsonl")
    parser.add_argument("--print-top", type=int, default=20, help="Print top N records to stdout.")
    args = parser.parse_args()

    settings = Settings()
    queue = build_queue(settings, args.limit_scan, args.top_k)
    write_jsonl(Path(args.output), queue)

    out = {
        "scanned_limit": args.limit_scan,
        "queue_size": len(queue),
        "output": args.output,
    }
    print(json.dumps(out, indent=2))
    if args.print_top > 0:
        print(json.dumps(queue[: args.print_top], indent=2))


if __name__ == "__main__":
    main()

