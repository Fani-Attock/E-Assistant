import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from src.core.logging_utils import setup_logging


logger = setup_logging("jobs.run_retrain_scheduler")
ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "jobs"


def run_once(args, env: dict[str, str]) -> None:
    cmd = [
        sys.executable,
        str(JOBS / "retrain_policy.py"),
        "--execute",
        "--min-new-labels",
        str(args.min_new_labels),
        "--min-new-interactions",
        str(args.min_new_interactions),
        "--max-model-age-days",
        str(args.max_model_age_days),
        "--matcher-split-mode",
        args.matcher_split_mode,
    ]
    if args.matcher_cross_source_val_only:
        cmd.append("--matcher-cross-source-val-only")
    if args.only_real_interactions:
        cmd.append("--only-real-interactions")
    logger.info("Running retrain policy: %s", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=str(ROOT), env=env)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run retrain policy on a schedule.")
    parser.add_argument("--once", action="store_true", help="Execute one retrain-policy check and exit.")
    parser.add_argument("--interval-seconds", type=int, default=21600, help="Scheduler interval (default: 6 hours).")
    parser.add_argument("--min-new-labels", type=int, default=200)
    parser.add_argument("--min-new-interactions", type=int, default=1000)
    parser.add_argument("--max-model-age-days", type=int, default=14)
    parser.add_argument("--matcher-split-mode", choices=["stratified_hash", "time_based"], default="time_based")
    parser.add_argument("--matcher-cross-source-val-only", action="store_true")
    parser.add_argument("--only-real-interactions", action="store_true")
    args = parser.parse_args()

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) if not existing else f"{str(ROOT)}{os.pathsep}{existing}"
    env["PYTHONUNBUFFERED"] = "1"

    if args.once:
        run_once(args, env)
        return

    while True:
        try:
            run_once(args, env)
        except Exception:
            logger.exception("Retrain policy execution failed.")
        logger.info("Sleeping for %s seconds", args.interval_seconds)
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
