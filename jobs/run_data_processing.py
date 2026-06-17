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
    print("[OK] Data processing stage finished.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified data processing stage runner.")
    parser.add_argument("--with-init-db", action="store_true", help="Initialize Mongo indexes before processing.")
    parser.add_argument("--stale-hours", type=int, default=72, help="Stale window for cleanup.")
    parser.add_argument("--skip-cleanup", action="store_true", help="Skip stale cleanup.")
    args = parser.parse_args()

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) if not existing else f"{str(ROOT)}{os.pathsep}{existing}"
    env["PYTHONUNBUFFERED"] = "1"

    cmd = [sys.executable, str(JOBS / "build_dataset.py"), "--stale-hours", str(args.stale_hours)]
    if args.with_init_db:
        cmd.append("--with-init-db")
    if args.skip_cleanup:
        cmd.append("--skip-cleanup")

    run_step(cmd, env)


if __name__ == "__main__":
    main()

