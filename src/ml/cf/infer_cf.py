from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class CFRecommender:
    def __init__(self, model_dir: str):
        root = Path(model_dir)
        self.user_factors = np.load(root / "user_factors.npy")
        self.item_factors = np.load(root / "item_factors.npy")
        self.users = json.loads((root / "users.json").read_text(encoding="utf-8"))
        self.offers = json.loads((root / "offers.json").read_text(encoding="utf-8"))
        self.user_to_idx = {u: i for i, u in enumerate(self.users)}
        self.offer_to_idx = {o: i for i, o in enumerate(self.offers)}

    def score_user_items(self, user_id: str, offer_ids: list[str]) -> dict[str, float]:
        idx = self.user_to_idx.get(user_id)
        if idx is None:
            return {oid: 0.0 for oid in offer_ids}
        u_vec = self.user_factors[idx]
        out: dict[str, float] = {}
        for oid in offer_ids:
            j = self.offer_to_idx.get(oid)
            if j is None:
                out[oid] = 0.0
            else:
                out[oid] = float(np.dot(u_vec, self.item_factors[j]))
        return out

    @staticmethod
    def normalize_scores(scores: dict[str, float]) -> dict[str, float]:
        if not scores:
            return scores
        values = np.array(list(scores.values()), dtype=np.float32)
        lo = float(values.min())
        hi = float(values.max())
        if hi - lo < 1e-9:
            return {k: 0.0 for k in scores}
        return {k: float((v - lo) / (hi - lo)) for k, v in scores.items()}
