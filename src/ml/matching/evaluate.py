from __future__ import annotations

from typing import Sequence

import numpy as np
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

from src.ml.matching.infer import ProductMatcher


def score_pairs(pairs: Sequence[dict], model_path: str) -> tuple[np.ndarray, np.ndarray]:
    matcher = ProductMatcher(model_path)
    text_a = [p["text_a"] for p in pairs]
    text_b = [p["text_b"] for p in pairs]
    labels = np.array([int(float(p["label"]) >= 0.5) for p in pairs], dtype=np.int32)

    emb_a = matcher.encode(text_a)
    emb_b = matcher.encode(text_b)
    sims = (emb_a * emb_b).sum(axis=1).astype(np.float32)
    return labels, sims


def metrics_from_scores(labels: np.ndarray, sims: np.ndarray, threshold: float) -> dict:
    preds = [1 if s >= threshold else 0 for s in sims]

    return {
        "count": len(labels),
        "threshold": threshold,
        "accuracy": float(accuracy_score(labels, preds)),
        "precision": float(precision_score(labels, preds, zero_division=0)),
        "recall": float(recall_score(labels, preds, zero_division=0)),
        "f1": float(f1_score(labels, preds, zero_division=0)),
    }


def evaluate_pairs(pairs: Sequence[dict], model_path: str, threshold: float = 0.7) -> dict:
    labels, sims = score_pairs(pairs, model_path)
    return metrics_from_scores(labels, sims, threshold)
