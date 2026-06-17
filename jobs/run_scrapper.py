import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "jobs"


def run_step(cmd: list[str], env: dict[str, str]) -> None:
    print(f"[STEP] {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True, cwd=str(ROOT), env=env)
    print("[OK] Scraping stage finished.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified scraping stage runner.")
    parser.add_argument("--once", action="store_true", help="Run one scraper cycle and exit.")
    parser.add_argument("--interval-seconds", type=int, default=10800, help="Loop interval when --once is not set.")
    parser.add_argument(
        "--include-requests",
        action="store_true",
        help="Include ishopping requests_bs4 scraper (can be blocked with 403).",
    )
    args = parser.parse_args()

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) if not existing else f"{str(ROOT)}{os.pathsep}{existing}"
    env["PYTHONUNBUFFERED"] = "1"

    cmd = [sys.executable, str(JOBS / "run_scrapers.py")]
    if args.once:
        cmd.append("--once")
    if args.include_requests:
        cmd.append("--include-requests")
    if not args.once and args.interval_seconds:
        cmd.extend(["--interval-seconds", str(args.interval_seconds)])

    run_step(cmd, env)


if __name__ == "__main__":
    main()

