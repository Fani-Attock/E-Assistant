import argparse
import os
import subprocess
import sys
from time import perf_counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = ROOT / "jobs"


def run_step(cmd: list[str], env: dict[str, str]) -> None:
    started = perf_counter()
    print(f"[STEP] {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=str(ROOT), env=env)
    print(f"[OK] {Path(cmd[1]).name} finished in {perf_counter() - started:.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build unified dataset: ingest -> normalize -> cleanup -> recluster"
    )
    parser.add_argument("--stale-hours", type=int, default=72, help="Stale window for cleanup step")
    parser.add_argument("--skip-cleanup", action="store_true", help="Skip stale cleanup step")
    parser.add_argument("--with-init-db", action="store_true", help="Run init_db before dataset steps")
    args = parser.parse_args()

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) if not existing else f"{str(ROOT)}{os.pathsep}{existing}"
    env["PYTHONUNBUFFERED"] = "1"

    if args.with_init_db:
        run_step([sys.executable, str(JOBS_DIR / "init_db.py")], env)

    run_step([sys.executable, str(JOBS_DIR / "ingest_sources.py")], env)
    run_step([sys.executable, str(JOBS_DIR / "normalize_offers.py")], env)

    if not args.skip_cleanup:
        run_step(
            [
                sys.executable,
                str(JOBS_DIR / "cleanup_stale_offers.py"),
                "--stale-hours",
                str(args.stale_hours),
            ],
            env,
        )

    run_step([sys.executable, str(JOBS_DIR / "recluster_offers.py")], env)
    print("[DONE] Unified dataset build completed.")


if __name__ == "__main__":
    main()
