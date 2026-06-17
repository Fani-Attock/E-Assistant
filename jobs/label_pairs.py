import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient, UpdateOne
from tqdm import tqdm

from src.core.normalize import extract_brand, normalize_text
from src.core.settings import Settings


STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "inch",
    "inches",
    "new",
    "latest",
    "original",
    "official",
    "edition",
    "series",
}


def _tokenize(text: str) -> set[str]:
    tokens = [t for t in normalize_text(text).split() if len(t) > 1 and t not in STOPWORDS]
    return set(tokens)


def _extract_numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", normalize_text(text)))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _auto_decision(row: dict, min_pos_jaccard: float, max_neg_jaccard: float) -> tuple[int, float, str, float] | None:
    title_a = str(row.get("title_a", ""))
    title_b = str(row.get("title_b", ""))
    auto_label = row.get("auto_label")

    tokens_a = _tokenize(title_a)
    tokens_b = _tokenize(title_b)
    jaccard = _jaccard(tokens_a, tokens_b)
    nums_a = _extract_numbers(title_a)
    nums_b = _extract_numbers(title_b)
    brand_a = extract_brand(title_a)
    brand_b = extract_brand(title_b)
    brands_match = brand_a == brand_b and brand_a is not None

    if auto_label == 1:
        # Prevent false positives only when numeric tokens fully conflict.
        if nums_a and nums_b and nums_a.isdisjoint(nums_b):
            return None
        if jaccard >= min_pos_jaccard:
            confidence = min(0.99, 0.70 + 0.30 * jaccard)
            return 1, confidence, "auto_canonical_pos", jaccard
        if brands_match and nums_a and nums_b and nums_a == nums_b and jaccard >= max(0.35, min_pos_jaccard - 0.12):
            confidence = min(0.96, 0.66 + 0.28 * jaccard)
            return 1, confidence, "auto_brand_num_pos", jaccard

    if auto_label == 0:
        brand_conflict = bool(brand_a and brand_b and brand_a != brand_b)
        num_disjoint = bool(nums_a and nums_b and nums_a.isdisjoint(nums_b))

        if num_disjoint and jaccard <= max(0.20, max_neg_jaccard + 0.08):
            confidence = min(0.99, 0.80 + 0.20 * (1.0 - jaccard))
            return 0, confidence, "auto_num_disjoint_neg", jaccard
        if brand_conflict and jaccard <= max(0.24, max_neg_jaccard + 0.12):
            confidence = min(0.97, 0.78 + 0.19 * (1.0 - jaccard))
            return 0, confidence, "auto_brand_conflict_neg", jaccard
        if jaccard <= max_neg_jaccard:
            confidence = min(0.90, 0.70 + 0.20 * (1.0 - jaccard))
            return 0, confidence, "auto_low_overlap_neg", jaccard

    return None


def _print_stats(col) -> None:
    stats = {
        "total_pairs": col.count_documents({}),
        "unlabeled": col.count_documents({"label": None}),
        "labeled": col.count_documents({"label": {"$in": [0, 1]}}),
        "positive": col.count_documents({"label": 1}),
        "negative": col.count_documents({"label": 0}),
        "auto_labeled": col.count_documents({"label_source": {"$regex": "^auto_"}}),
        "manual_labeled": col.count_documents({"label_source": "manual_cli"}),
        "manual_imported": col.count_documents({"label_source": "manual_import"}),
        "candidate_positive_unlabeled": col.count_documents({"label": None, "auto_label": 1}),
    }
    print(json.dumps(stats, indent=2))


def _list_pairs(col, query: dict, limit: int) -> None:
    rows = list(
        col.find(
            query,
            {
                "_id": 0,
                "pair_id": 1,
                "title_a": 1,
                "title_b": 1,
                "source_a": 1,
                "source_b": 1,
                "auto_label": 1,
                "label": 1,
                "label_source": 1,
                "label_confidence": 1,
            },
        ).limit(limit)
    )
    print(json.dumps(rows, indent=2))


def _export_jsonl(col, output_path: Path, limit: int) -> None:
    cursor = col.find(
        {"label": {"$in": [0, 1]}},
        {
            "_id": 0,
            "pair_id": 1,
            "title_a": 1,
            "title_b": 1,
            "source_a": 1,
            "source_b": 1,
            "label": 1,
            "label_source": 1,
            "labeled_at": 1,
            "created_at": 1,
        },
    ).limit(limit)
    written = 0
    with output_path.open("w", encoding="utf-8") as f:
        for row in tqdm(cursor, desc="export_pairs", unit="pair"):
            record = {
                "pair_id": row["pair_id"],
                "text_a": row["title_a"],
                "text_b": row["title_b"],
                "source_a": row.get("source_a"),
                "source_b": row.get("source_b"),
                "label": int(row["label"]),
                "label_source": row.get("label_source"),
                "labeled_at": row.get("labeled_at"),
                "created_at": row.get("created_at"),
            }
            f.write(json.dumps(record, ensure_ascii=True) + "\n")
            written += 1
    print(json.dumps({"exported": written, "output": str(output_path)}, indent=2))


def _import_decisions(col, path: Path, dry_run: bool) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Decision file not found: {path}")
    updates: list[UpdateOne] = []
    seen = 0
    applied = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            seen += 1
            row = json.loads(line)
            pair_id = str(row.get("pair_id") or "").strip()
            if not pair_id:
                continue
            label_val = row.get("label")
            if label_val not in (0, 1, "0", "1"):
                continue
            label = int(label_val)
            reviewer = str(row.get("reviewer") or "manual_import").strip() or "manual_import"
            updates.append(
                UpdateOne(
                    {"pair_id": pair_id},
                    {
                        "$set": {
                            "label": label,
                            "label_source": "manual_import",
                            "label_confidence": 1.0,
                            "labeled_at": datetime.now(timezone.utc),
                            "reviewer": reviewer,
                        }
                    },
                    upsert=False,
                )
            )
            if len(updates) >= 1000:
                if not dry_run:
                    result = col.bulk_write(updates, ordered=False)
                    applied += result.modified_count
                else:
                    applied += len(updates)
                updates.clear()
    if updates:
        if not dry_run:
            result = col.bulk_write(updates, ordered=False)
            applied += result.modified_count
        else:
            applied += len(updates)
    print(json.dumps({"decision_rows_seen": seen, "updates_applied": applied, "dry_run": dry_run}, indent=2))


def _auto_label(col, args) -> None:
    projection = {"_id": 0, "pair_id": 1, "title_a": 1, "title_b": 1, "auto_label": 1}
    cursor = col.find({"label": None}, projection).limit(args.auto_limit)
    now = datetime.now(timezone.utc)

    pos = 0
    neg = 0
    inspected = 0
    updates = 0
    batch: list[UpdateOne] = []
    review_count = 0

    review_fp = None
    if args.review_file:
        review_path = Path(args.review_file)
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_fp = review_path.open("w", encoding="utf-8")

    progress = tqdm(cursor, total=args.auto_limit, desc="auto_label", unit="pair")
    try:
        for row in progress:
            inspected += 1
            decision = _auto_decision(row, args.min_pos_jaccard, args.max_neg_jaccard)
            if decision is None:
                if review_fp is not None:
                    review_fp.write(json.dumps(row, ensure_ascii=True) + "\n")
                    review_count += 1
                continue

            label, confidence, source, jaccard = decision
            if label == 1 and pos >= args.auto_max_pos:
                continue
            if label == 0 and neg >= args.auto_max_neg:
                continue
            if updates >= args.auto_max_updates:
                break

            if label == 1:
                pos += 1
            else:
                neg += 1
            updates += 1

            if not args.dry_run:
                batch.append(
                    UpdateOne(
                        {"pair_id": row["pair_id"], "label": None},
                        {
                            "$set": {
                                "label": int(label),
                                "label_source": source,
                                "label_confidence": round(float(confidence), 4),
                                "label_jaccard": round(float(jaccard), 4),
                                "labeled_at": now,
                            }
                        },
                        upsert=False,
                    )
                )
                if len(batch) >= 1000:
                    col.bulk_write(batch, ordered=False)
                    batch.clear()

            if inspected % 25 == 0:
                progress.set_postfix(inspected=inspected, updates=updates, pos=pos, neg=neg)
    finally:
        progress.close()
        if review_fp is not None:
            review_fp.close()

    if batch:
        col.bulk_write(batch, ordered=False)
    progress.set_postfix(inspected=inspected, updates=updates, pos=pos, neg=neg)

    print(
        json.dumps(
            {
                "inspected": inspected,
                "auto_updates": updates,
                "positive": pos,
                "negative": neg,
                "dry_run": args.dry_run,
                "review_saved": bool(args.review_file),
                "review_count": review_count,
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Label and inspect match pairs.")
    parser.add_argument("--stats", action="store_true", help="Show labeling statistics")
    parser.add_argument("--list-unlabeled", action="store_true")
    parser.add_argument("--list-labeled", action="store_true")
    parser.add_argument("--list-candidate-positives", action="store_true", help="List unlabeled likely-positive pairs.")
    parser.add_argument("--limit", type=int, default=20)

    parser.add_argument("--pair-id", default="")
    parser.add_argument("--label", type=int, choices=[0, 1], default=None)

    parser.add_argument("--auto-label", action="store_true", help="Apply high-confidence auto labels to unlabeled pairs")
    parser.add_argument("--auto-limit", type=int, default=5000)
    parser.add_argument("--auto-max-updates", type=int, default=1000)
    parser.add_argument("--auto-max-pos", type=int, default=500)
    parser.add_argument("--auto-max-neg", type=int, default=500)
    parser.add_argument("--min-pos-jaccard", type=float, default=0.52)
    parser.add_argument("--max-neg-jaccard", type=float, default=0.10)
    parser.add_argument("--review-file", default="", help="Optional JSONL path to dump uncertain unlabeled pairs")
    parser.add_argument("--dry-run", action="store_true")

    parser.add_argument("--export-jsonl", default="", help="Export labeled pairs to JSONL path")
    parser.add_argument("--export-limit", type=int, default=200000)
    parser.add_argument("--import-decisions", default="", help="Import manual label decisions JSONL")

    args = parser.parse_args()

    settings = Settings()
    col = MongoClient(settings.mongo_uri)[settings.app_db_name][settings.match_pairs_collection]

    if args.stats:
        _print_stats(col)
        return

    if args.list_unlabeled:
        _list_pairs(col, {"label": None}, args.limit)
        return

    if args.list_labeled:
        _list_pairs(col, {"label": {"$in": [0, 1]}}, args.limit)
        return

    if args.list_candidate_positives:
        _list_pairs(col, {"label": None, "auto_label": 1}, args.limit)
        return

    if args.pair_id and args.label is not None:
        result = col.update_one(
            {"pair_id": args.pair_id},
            {
                "$set": {
                    "label": int(args.label),
                    "label_source": "manual_cli",
                    "label_confidence": 1.0,
                    "labeled_at": datetime.now(timezone.utc),
                }
            },
        )
        print(json.dumps({"matched": result.matched_count, "modified": result.modified_count}))
        return

    if args.auto_label:
        _auto_label(col, args)
        return

    if args.export_jsonl:
        _export_jsonl(col, Path(args.export_jsonl), args.export_limit)
        return

    if args.import_decisions:
        _import_decisions(col, Path(args.import_decisions), args.dry_run)
        return

    raise SystemExit(
        "Use one action: --stats | --list-unlabeled | --list-labeled | --list-candidate-positives | "
        "--pair-id <id> --label <0|1> | --auto-label | --export-jsonl <path> | --import-decisions <path>"
    )


if __name__ == "__main__":
    main()
