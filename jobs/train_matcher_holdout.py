import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "jobs"


def run_step(cmd: list[str], env: dict[str, str]) -> None:
    print(f"[STEP] {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=str(ROOT), env=env)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run matcher holdout flow: prepare split -> train -> report.")
    parser.add_argument("--data-dir", default="artifacts/matcher_data")
    parser.add_argument("--model-output", default="artifacts/matching_model")
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--split-mode", choices=["stratified_hash", "time_based"], default="time_based")
    parser.add_argument(
        "--cross-source-val-only",
        action="store_true",
        help="Use cross-source pairs only for validation split where possible.",
    )
    parser.add_argument("--limit", type=int, default=200000)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--threshold", type=float, default=0.65)
    parser.add_argument("--base-model", default="sentence-transformers/all-MiniLM-L6-v2")
    args = parser.parse_args()

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) if not existing else f"{str(ROOT)}{os.pathsep}{existing}"
    env["PYTHONUNBUFFERED"] = "1"

    data_dir = Path(args.data_dir)
    train_path = data_dir / "train.jsonl"
    val_path = data_dir / "val.jsonl"
    report_path = data_dir / "holdout_report.json"

    prepare_cmd = [
        sys.executable,
        str(JOBS / "prepare_matcher_dataset.py"),
        "--output-dir",
        str(data_dir),
        "--val-ratio",
        str(args.val_ratio),
        "--seed",
        str(args.seed),
        "--split-mode",
        str(args.split_mode),
        "--limit",
        str(args.limit),
    ]
    if args.cross_source_val_only:
        prepare_cmd.append("--cross-source-val-only")
    run_step(prepare_cmd, env)
    run_step(
        [
            sys.executable,
            str(JOBS / "train_matcher.py"),
            "--train-data",
            str(train_path),
            "--val-data",
            str(val_path),
            "--output",
            str(args.model_output),
            "--epochs",
            str(args.epochs),
            "--batch-size",
            str(args.batch_size),
            "--base-model",
            str(args.base_model),
            "--limit",
            str(args.limit),
        ],
        env,
    )
    run_step(
        [
            sys.executable,
            str(JOBS / "report_matcher_holdout.py"),
            "--model",
            str(args.model_output),
            "--train-data",
            str(train_path),
            "--val-data",
            str(val_path),
            "--threshold",
            str(args.threshold),
            "--output-json",
            str(report_path),
            "--limit",
            str(args.limit),
        ],
        env,
    )
    print(f"[DONE] Matcher holdout flow completed. Report: {report_path}")


if __name__ == "__main__":
    main()
