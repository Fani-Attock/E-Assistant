import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pymongo import MongoClient

from src.core.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Decide whether to retrain matcher/cf based on data drift and volume.")
    parser.add_argument("--execute", action="store_true", help="Execute suggested retraining commands")
    parser.add_argument("--min-new-labels", type=int, default=500)
    parser.add_argument("--min-new-interactions", type=int, default=1000)
    parser.add_argument("--max-model-age-days", type=int, default=14)
    parser.add_argument("--matcher-split-mode", choices=["stratified_hash", "time_based"], default="time_based")
    parser.add_argument("--matcher-cross-source-val-only", action="store_true")
    parser.add_argument(
        "--only-real-interactions",
        action="store_true",
        help="Count only real interactions (is_synthetic != true) for CF retrain decisions.",
    )
    args = parser.parse_args()

    settings = Settings()
    db = MongoClient(settings.mongo_uri)[settings.app_db_name]
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=args.max_model_age_days)

    labels_new = db[settings.match_pairs_collection].count_documents(
        {
            "label": {"$in": [0, 1]},
            "$or": [{"labeled_at": {"$gte": since}}, {"labeled_at": {"$exists": False}, "created_at": {"$gte": since}}],
        }
    )
    interaction_query: dict[str, Any] = {"event_ts": {"$gte": since}}
    if args.only_real_interactions:
        interaction_query["$or"] = [{"is_synthetic": {"$exists": False}}, {"is_synthetic": False}]
    interactions_new = db[settings.interactions_collection].count_documents(interaction_query)

    matcher_path = Path("artifacts/matching_model")
    cf_path = Path("artifacts/cf_model")
    matcher_old = (not matcher_path.exists()) or (
        datetime.fromtimestamp(matcher_path.stat().st_mtime, tz=timezone.utc) < since
    )
    cf_old = (not cf_path.exists()) or (datetime.fromtimestamp(cf_path.stat().st_mtime, tz=timezone.utc) < since)

    actions: list[list[str]] = []
    if labels_new >= args.min_new_labels or matcher_old:
        matcher_cmd = [
            sys.executable,
            "jobs/train_matcher_holdout.py",
            "--model-output",
            "artifacts/matching_model",
            "--split-mode",
            args.matcher_split_mode,
        ]
        if args.matcher_cross_source_val_only:
            matcher_cmd.append("--cross-source-val-only")
        actions.append(matcher_cmd)
    if interactions_new >= args.min_new_interactions or cf_old:
        cf_cmd = [sys.executable, "jobs/train_cf.py", "--output", "artifacts/cf_model"]
        if args.only_real_interactions:
            cf_cmd.append("--only-real")
        actions.append(cf_cmd)

    report = {
        "labels_new": labels_new,
        "interactions_new": interactions_new,
        "only_real_interactions": args.only_real_interactions,
        "matcher_split_mode": args.matcher_split_mode,
        "matcher_cross_source_val_only": args.matcher_cross_source_val_only,
        "matcher_old": matcher_old,
        "cf_old": cf_old,
        "actions": actions,
    }
    print(json.dumps(report, indent=2))

    if args.execute:
        for cmd in actions:
            subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
