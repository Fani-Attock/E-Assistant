import argparse
import subprocess
import sys
import time
from pathlib import Path

from tqdm import tqdm

from src.core.logging_utils import setup_logging

logger = setup_logging("jobs.run_pipeline")

ROOT = Path(__file__).resolve().parents[1]
STEPS = [
    ROOT / "jobs" / "init_db.py",
    ROOT / "jobs" / "ingest_sources.py",
    ROOT / "jobs" / "normalize_offers.py",
    ROOT / "jobs" / "cleanup_stale_offers.py",
    ROOT / "jobs" / "recluster_offers.py",
]


def run_once() -> None:
    for step in tqdm(STEPS, desc="pipeline-steps", unit="step"):
        logger.info("Running step: %s", step.name)
        subprocess.run([sys.executable, str(step)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run end-to-end ingestion + normalization pipeline.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=3600)
    args = parser.parse_args()

    if args.once:
        run_once()
        return

    while True:
        run_once()
        logger.info("Sleeping for %s seconds", args.interval_seconds)
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
