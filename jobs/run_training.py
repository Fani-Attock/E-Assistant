import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from pymongo import MongoClient

from src.core.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "jobs"


def run_step(cmd: list[str], env: dict[str, str]) -> None:
    print(f"[STEP] {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True, cwd=str(ROOT), env=env)


def get_counts(settings: Settings, *, only_real_interactions: bool = False) -> tuple[int, int, int]:
    db = MongoClient(settings.mongo_uri)[settings.app_db_name]
    labeled = db[settings.match_pairs_collection].count_documents({"label": {"$in": [0, 1]}})
    query: dict[str, Any] = {}
    if only_real_interactions:
        query = {"$or": [{"is_synthetic": {"$exists": False}}, {"is_synthetic": False}]}
    interactions = db[settings.interactions_collection].count_documents(query)
    marketplace_products = (
        db[settings.marketplace_seller_products_collection].count_documents({"status": "active"})
        + db[settings.normalized_collection].count_documents({"in_stock": True})
    )
    return labeled, interactions, marketplace_products


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified training stage runner (matcher + CF).")

    parser.add_argument("--prepare-labels", action="store_true", help="Build candidates and auto-label before matcher.")
    parser.add_argument("--max-per-brand", type=int, default=200)
    parser.add_argument("--auto-label-limit", type=int, default=5000)
    parser.add_argument("--auto-max-updates", type=int, default=600)
    parser.add_argument("--auto-max-pos", type=int, default=300)
    parser.add_argument("--auto-max-neg", type=int, default=300)
    parser.add_argument("--min-pos-jaccard", type=float, default=0.52)
    parser.add_argument("--max-neg-jaccard", type=float, default=0.10)
    parser.add_argument(
        "--build-positive-review-queue",
        action="store_true",
        help="Generate prioritized manual-positive review queue after auto-labeling.",
    )
    parser.add_argument("--positive-review-top-k", type=int, default=300)

    parser.add_argument("--skip-matcher", action="store_true")
    parser.add_argument("--matcher-data-dir", default="artifacts/matcher_data")
    parser.add_argument("--matcher-model-output", default="artifacts/matching_model")
    parser.add_argument("--matcher-val-ratio", type=float, default=0.2)
    parser.add_argument("--matcher-seed", type=int, default=42)
    parser.add_argument("--matcher-limit", type=int, default=200000)
    parser.add_argument("--matcher-epochs", type=int, default=2)
    parser.add_argument("--matcher-batch-size", type=int, default=32)
    parser.add_argument("--matcher-threshold", type=float, default=0.65)
    parser.add_argument("--matcher-base-model", default="sentence-transformers/all-MiniLM-L6-v2")
    parser.add_argument("--matcher-split-mode", choices=["stratified_hash", "time_based"], default="time_based")
    parser.add_argument("--matcher-cross-source-val-only", action="store_true")

    parser.add_argument("--skip-cf", action="store_true")
    parser.add_argument("--bootstrap-interactions", action="store_true")
    parser.add_argument("--bootstrap-users", type=int, default=250)
    parser.add_argument("--bootstrap-events-per-user", type=int, default=24)
    parser.add_argument("--bootstrap-lookback-days", type=int, default=30)
    parser.add_argument("--bootstrap-seed", type=int, default=42)
    parser.add_argument("--bootstrap-clear-existing", action="store_true")
    parser.add_argument("--min-interactions-for-cf", type=int, default=200)
    parser.add_argument("--cf-output", default="artifacts/cf_model")
    parser.add_argument("--cf-components", type=int, default=32)
    parser.add_argument("--cf-k", type=int, default=10)
    parser.add_argument("--cf-max-users", type=int, default=1000)
    parser.add_argument(
        "--ingest-real-interactions-file",
        default=None,
        help="Optional CSV/JSON/JSONL file to ingest real interactions before CF stage.",
    )
    parser.add_argument(
        "--ingest-real-interactions-format",
        choices=["auto", "csv", "json", "jsonl"],
        default="auto",
    )
    parser.add_argument("--ingest-real-batch-size", type=int, default=1000)
    parser.add_argument("--ingest-real-max-events", type=int, default=0)
    parser.add_argument("--ingest-real-default-source", default=None)
    parser.add_argument(
        "--ingest-real-default-event-type",
        choices=["view", "click", "save", "purchase"],
        default="view",
    )
    parser.add_argument("--ingest-real-strict", action="store_true")
    parser.add_argument(
        "--cf-only-real",
        action="store_true",
        help="Train/evaluate CF using only real interactions (is_synthetic != true).",
    )
    parser.add_argument("--skip-marketplace-dl", action="store_true")
    parser.add_argument("--marketplace-dl-output", default=None)
    parser.add_argument("--marketplace-dl-epochs", type=int, default=20)
    parser.add_argument("--marketplace-dl-batch-size", type=int, default=32)
    parser.add_argument("--marketplace-dl-learning-rate", type=float, default=0.001)
    parser.add_argument("--marketplace-dl-hidden-dim", type=int, default=128)

    args = parser.parse_args()

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) if not existing else f"{str(ROOT)}{os.pathsep}{existing}"
    env["PYTHONUNBUFFERED"] = "1"

    settings = Settings()

    if args.prepare_labels:
        run_step(
            [
                sys.executable,
                str(JOBS / "build_match_candidates.py"),
                "--max-per-brand",
                str(args.max_per_brand),
                "--include-same-source-positives",
            ],
            env,
        )
        run_step(
            [
                sys.executable,
                str(JOBS / "label_pairs.py"),
                "--auto-label",
                "--auto-limit",
                str(args.auto_label_limit),
                "--auto-max-updates",
                str(args.auto_max_updates),
                "--auto-max-pos",
                str(args.auto_max_pos),
                "--auto-max-neg",
                str(args.auto_max_neg),
                "--min-pos-jaccard",
                str(args.min_pos_jaccard),
                "--max-neg-jaccard",
                str(args.max_neg_jaccard),
                "--review-file",
                "artifacts/labeling/review_unlabeled.jsonl",
            ],
            env,
        )
        if args.build_positive_review_queue:
            run_step(
                [
                    sys.executable,
                    str(JOBS / "build_positive_review_queue.py"),
                    "--top-k",
                    str(args.positive_review_top_k),
                    "--output",
                    "artifacts/labeling/manual_positive_review_queue.jsonl",
                ],
                env,
            )

    if not args.skip_matcher:
        labeled, _, _ = get_counts(settings)
        if labeled < 100:
            raise RuntimeError(
                f"Not enough labeled pairs for matcher training: labeled={labeled}. "
                "Use --prepare-labels and/or add manual labels first."
            )
        matcher_cmd = [
            sys.executable,
            str(JOBS / "train_matcher_holdout.py"),
            "--data-dir",
            args.matcher_data_dir,
            "--model-output",
            args.matcher_model_output,
            "--val-ratio",
            str(args.matcher_val_ratio),
            "--seed",
            str(args.matcher_seed),
            "--limit",
            str(args.matcher_limit),
            "--epochs",
            str(args.matcher_epochs),
            "--batch-size",
            str(args.matcher_batch_size),
            "--threshold",
            str(args.matcher_threshold),
            "--base-model",
            args.matcher_base_model,
            "--split-mode",
            args.matcher_split_mode,
        ]
        if args.matcher_cross_source_val_only:
            matcher_cmd.append("--cross-source-val-only")
        run_step(matcher_cmd, env)

    if not args.skip_cf:
        if args.ingest_real_interactions_file:
            ingest_cmd = [
                sys.executable,
                str(JOBS / "ingest_real_interactions.py"),
                "--input",
                args.ingest_real_interactions_file,
                "--format",
                args.ingest_real_interactions_format,
                "--batch-size",
                str(args.ingest_real_batch_size),
                "--default-event-type",
                args.ingest_real_default_event_type,
            ]
            if args.ingest_real_max_events > 0:
                ingest_cmd.extend(["--max-events", str(args.ingest_real_max_events)])
            if args.ingest_real_default_source:
                ingest_cmd.extend(["--default-source", args.ingest_real_default_source])
            if args.ingest_real_strict:
                ingest_cmd.append("--strict")
            run_step(ingest_cmd, env)
            if not args.cf_only_real:
                print(
                    "[INFO] Real-interactions ingest file provided; enabling real-only CF mode.",
                    flush=True,
                )
                args.cf_only_real = True

        _, interactions, _ = get_counts(settings, only_real_interactions=args.cf_only_real)
        if args.cf_only_real and args.bootstrap_interactions and interactions < args.min_interactions_for_cf:
            print(
                "[WARN] --cf-only-real is enabled; bootstrap interactions are synthetic and will not satisfy real-data threshold.",
                flush=True,
            )
        if interactions < args.min_interactions_for_cf and args.bootstrap_interactions:
            cmd = [
                sys.executable,
                str(JOBS / "bootstrap_interactions.py"),
                "--users",
                str(args.bootstrap_users),
                "--events-per-user",
                str(args.bootstrap_events_per_user),
                "--lookback-days",
                str(args.bootstrap_lookback_days),
                "--seed",
                str(args.bootstrap_seed),
            ]
            if args.bootstrap_clear_existing:
                cmd.append("--clear-existing")
            run_step(cmd, env)
            _, interactions, _ = get_counts(settings, only_real_interactions=args.cf_only_real)

        if interactions < args.min_interactions_for_cf:
            raise RuntimeError(
                f"Not enough interactions for CF training: interactions={interactions}. "
                f"Need at least {args.min_interactions_for_cf}, or run with --bootstrap-interactions."
            )

        train_cf_cmd = [
            sys.executable,
            str(JOBS / "train_cf.py"),
            "--output",
            args.cf_output,
            "--components",
            str(args.cf_components),
        ]
        eval_cf_cmd = [
            sys.executable,
            str(JOBS / "evaluate_cf.py"),
            "--model-dir",
            args.cf_output,
            "--k",
            str(args.cf_k),
            "--max-users",
            str(args.cf_max_users),
        ]
        if args.cf_only_real:
            train_cf_cmd.append("--only-real")
            eval_cf_cmd.append("--only-real")
        run_step(train_cf_cmd, env)
        run_step(eval_cf_cmd, env)

    if not args.skip_marketplace_dl:
        _, _, marketplace_products = get_counts(settings)
        if marketplace_products < 1:
            raise RuntimeError("No marketplace products available for marketplace DL training.")
        train_marketplace_cmd = [
            sys.executable,
            str(JOBS / "train_marketplace_dl.py"),
            "--epochs",
            str(args.marketplace_dl_epochs),
            "--batch-size",
            str(args.marketplace_dl_batch_size),
            "--learning-rate",
            str(args.marketplace_dl_learning_rate),
            "--hidden-dim",
            str(args.marketplace_dl_hidden_dim),
        ]
        if args.marketplace_dl_output:
            train_marketplace_cmd.extend(["--output", args.marketplace_dl_output])
        run_step(train_marketplace_cmd, env)
        run_step([sys.executable, str(JOBS / "run_marketplace_predictions.py")], env)

    print("[DONE] Training stage completed.", flush=True)


if __name__ == "__main__":
    main()
