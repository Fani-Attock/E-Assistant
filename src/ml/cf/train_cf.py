from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from pymongo import MongoClient
from scipy.sparse import csr_matrix
from sklearn.decomposition import TruncatedSVD

from src.core.settings import Settings


def _build_matrix(settings: Settings, *, only_real: bool = False) -> tuple[csr_matrix, list[str], list[str], dict[str, Any]]:
    client = MongoClient(settings.mongo_uri)
    db = client[settings.app_db_name]
    query: dict[str, Any] = {"user_id": {"$ne": None}, "offer_id": {"$ne": None}}
    if only_real:
        query["$or"] = [{"is_synthetic": {"$exists": False}}, {"is_synthetic": False}]
    interactions = list(
        db[settings.interactions_collection].find(
            query,
            {"_id": 0, "user_id": 1, "offer_id": 1, "weight": 1, "is_synthetic": 1},
        )
    )
    if not interactions:
        mode = "real-only" if only_real else "all"
        raise RuntimeError(f"No interactions available for CF training (mode={mode}).")

    users = sorted({x["user_id"] for x in interactions})
    offers = sorted({x["offer_id"] for x in interactions})
    user_to_idx = {u: i for i, u in enumerate(users)}
    offer_to_idx = {o: i for i, o in enumerate(offers)}

    row_idx = [user_to_idx[x["user_id"]] for x in interactions]
    col_idx = [offer_to_idx[x["offer_id"]] for x in interactions]
    values = [float(x.get("weight", 1.0)) for x in interactions]
    mat = csr_matrix((values, (row_idx, col_idx)), shape=(len(users), len(offers)), dtype=np.float32)
    real_count = sum(1 for x in interactions if not bool(x.get("is_synthetic", False)))
    synthetic_count = len(interactions) - real_count
    meta = {
        "interaction_count": len(interactions),
        "real_interactions": real_count,
        "synthetic_interactions": synthetic_count,
        "only_real": only_real,
    }
    return mat, users, offers, meta


def train_cf_model(settings: Settings, output_dir: str, n_components: int = 32, *, only_real: bool = False) -> dict:
    mat, users, offers, meta = _build_matrix(settings, only_real=only_real)
    if min(mat.shape) < 2:
        raise RuntimeError("Insufficient users/items for CF model.")
    n_components = max(2, min(n_components, min(mat.shape) - 1))
    svd = TruncatedSVD(n_components=n_components, random_state=42)
    user_factors = svd.fit_transform(mat)
    item_factors = svd.components_.T

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    np.save(out / "user_factors.npy", user_factors)
    np.save(out / "item_factors.npy", item_factors)
    with (out / "users.json").open("w", encoding="utf-8") as f:
        json.dump(users, f)
    with (out / "offers.json").open("w", encoding="utf-8") as f:
        json.dump(offers, f)
    with (out / "metadata.json").open("w", encoding="utf-8") as f:
        json.dump(
            {
                "users": len(users),
                "offers": len(offers),
                "components": n_components,
                **meta,
            },
            f,
            indent=2,
        )

    return {"users": len(users), "offers": len(offers), "components": n_components, **meta}
