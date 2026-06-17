import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.logging_utils import setup_logging
from src.core.settings import Settings

logger = setup_logging("jobs.run_scrapers")

STATE_FILE = ROOT / "artifacts" / "scraper_health.json"
DEFAULT_SCRIPTS = [
    ROOT / "src" / "scrapers" / "daraz" / "playwright_async.py",
    ROOT / "src" / "scrapers" / "ishopping" / "requests_bs4.py",
    ROOT / "src" / "scrapers" / "ishopping" / "playwright_sync.py",
    ROOT / "src" / "scrapers" / "shophive" / "playwright_async.py",
]


def script_id(script: Path) -> str:
    try:
        return script.relative_to(ROOT).as_posix()
    except Exception:
        return script.as_posix()


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"consecutive_failures": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def run_once(scripts: list[Path]) -> None:
    cfg = Settings()
    state = load_state()
    failures = state.setdefault("consecutive_failures", {})
    for script in tqdm(scripts, desc="scrapers", unit="script"):
        sid = script_id(script)
        logger.info("Running scraper: %s", sid)
        cmd = [sys.executable, str(script)]
        if script.name != "playwright_sync.py":
            cmd.append("--once")
        result = subprocess.run(cmd, check=False)
        name = sid
        if result.returncode != 0:
            logger.error("Scraper failed: %s returncode=%s", sid, result.returncode)
            failures[name] = int(failures.get(name, 0)) + 1
            if failures[name] >= cfg.scrape_fail_alert_threshold:
                logger.error(
                    "Scraper alert threshold reached: scraper=%s consecutive_failures=%s threshold=%s",
                    name,
                    failures[name],
                    cfg.scrape_fail_alert_threshold,
                )
        else:
            failures[name] = 0
    save_state(state)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run source scrapers.")
    parser.add_argument("--once", action="store_true", help="Run a single scrape cycle")
    parser.add_argument("--interval-seconds", type=int, default=10800)
    parser.add_argument(
        "--include-requests",
        action="store_true",
        help="Include ishopping requests_bs4 scraper (often blocked with 403).",
    )
    args = parser.parse_args()
    scripts = [s for s in DEFAULT_SCRIPTS if args.include_requests or s.name != "requests_bs4.py"]
    logger.info("Active scrapers: %s", [script_id(s) for s in scripts])

    if args.once:
        run_once(scripts)
        return

    while True:
        run_once(scripts)
        logger.info("Sleeping for %s seconds", args.interval_seconds)
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
