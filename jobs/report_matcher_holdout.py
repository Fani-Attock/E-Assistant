import argparse
import json
from pathlib import Path
from typing import Sequence

import numpy as np

from src.core.runtime_compat import patch_multiprocess_resource_tracker
from src.ml.matching.evaluate import metrics_from_scores, score_pairs


def load_pairs_from_jsonl(path: Path, limit: int) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("label") is None:
                continue
            rows.append(
                {
                    "text_a": row["text_a"],
                    "text_b": row["text_b"],
                    "label": float(row["label"]),
                    "source_a": row.get("source_a"),
                    "source_b": row.get("source_b"),
                }
            )
            if len(rows) >= limit:
                break
    return rows


def is_cross_source(row: dict) -> bool:
    a = str(row.get("source_a") or "").strip().lower()
    b = str(row.get("source_b") or "").strip().lower()
    return bool(a and b and a != b)


def eval_subset(labels: Sequence[int], sims: Sequence[float], threshold: float) -> dict:
    if len(labels) == 0:
        return {"count": 0}
    labels_arr = np.asarray(labels, dtype=np.int32)
    sims_arr = np.asarray(sims, dtype=np.float32)
    return metrics_from_scores(labels_arr, sims_arr, threshold)


def main() -> None:
    patch_multiprocess_resource_tracker()
    parser = argparse.ArgumentParser(description="Create train/validation holdout report for matcher.")
    parser.add_argument("--model", default="artifacts/matching_model")
    parser.add_argument("--train-data", required=True)
    parser.add_argument("--val-data", required=True)
    parser.add_argument("--limit", type=int, default=200000)
    parser.add_argument("--threshold", type=float, default=0.65)
    parser.add_argument("--sweep-start", type=float, default=0.45)
    parser.add_argument("--sweep-end", type=float, default=0.90)
    parser.add_argument("--sweep-step", type=float, default=0.01)
    parser.add_argument("--output-json", default="", help="Optional path to save report JSON")
    args = parser.parse_args()

    train_rows = load_pairs_from_jsonl(Path(args.train_data), args.limit)
    val_rows = load_pairs_from_jsonl(Path(args.val_data), args.limit)
    if not train_rows or not val_rows:
        raise RuntimeError("Both train and val split files must contain labeled rows.")

    train_labels, train_sims = score_pairs(train_rows, args.model)
    val_labels, val_sims = score_pairs(val_rows, args.model)

    train_metrics = metrics_from_scores(train_labels, train_sims, args.threshold)
    val_metrics = metrics_from_scores(val_labels, val_sims, args.threshold)

    cross_idx = [i for i, row in enumerate(val_rows) if is_cross_source(row)]
    same_idx = [i for i, row in enumerate(val_rows) if not is_cross_source(row)]
    val_cross_metrics = eval_subset(val_labels[cross_idx], val_sims[cross_idx], args.threshold)
    val_same_metrics = eval_subset(val_labels[same_idx], val_sims[same_idx], args.threshold)

    thresholds = []
    t = args.sweep_start
    while t <= args.sweep_end + 1e-9:
        thresholds.append(round(t, 4))
        t += args.sweep_step

    sweep = []
    for threshold in thresholds:
        m = metrics_from_scores(val_labels, val_sims, threshold)
        sweep.append(m)
    best = max(sweep, key=lambda m: (m["f1"], m["accuracy"], m["precision"], m["recall"]))

    report = {
        "model": args.model,
        "threshold_used": args.threshold,
        "train_count": len(train_rows),
        "val_count": len(val_rows),
        "val_cross_source_count": len(cross_idx),
        "val_same_source_count": len(same_idx),
        "train_metrics": train_metrics,
        "val_metrics": val_metrics,
        "val_cross_source_metrics": val_cross_metrics,
        "val_same_source_metrics": val_same_metrics,
        "generalization_gap_f1": float(train_metrics["f1"] - val_metrics["f1"]),
        "generalization_gap_accuracy": float(train_metrics["accuracy"] - val_metrics["accuracy"]),
        "best_val_threshold_by_f1": best["threshold"],
        "best_val_metrics": best,
    }

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
