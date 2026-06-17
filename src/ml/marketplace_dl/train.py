from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

from src.core.db import get_db
from src.core.marketplace_analytics import summarize_orders
from src.core.marketplace import serialize_scraped_catalog_product, serialize_seller_product, get_marketplace_projection
from src.core.settings import Settings
from src.ml.marketplace_dl.features import NUMERIC_DIM, TEXT_DIM, build_marketplace_feature_row
from src.ml.marketplace_dl.model import MarketplaceMultiTaskNet


class _MarketplaceDataset(Dataset):
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        row = self.rows[idx]
        return {
            "text": torch.tensor(row["text"], dtype=torch.float32),
            "numeric": torch.tensor(row["numeric"], dtype=torch.float32),
            "seller_id": torch.tensor(row["seller_id"], dtype=torch.long),
            "brand_id": torch.tensor(row["brand_id"], dtype=torch.long),
            "category_id": torch.tensor(row["category_id"], dtype=torch.long),
            "subcategory_id": torch.tensor(row["subcategory_id"], dtype=torch.long),
            "rating_target": torch.tensor(row["targets"]["rating"], dtype=torch.float32),
            "demand_target": torch.tensor(row["targets"]["demand"], dtype=torch.float32),
            "month_target": torch.tensor(row["targets"]["month_distribution"], dtype=torch.float32),
        }


@dataclass
class TrainingArtifacts:
    model_dir: str
    stats: dict[str, Any]


def _gather_marketplace_training_rows(settings: Settings) -> list[dict[str, Any]]:
    db = get_db(settings)
    product_rows: list[dict[str, Any]] = []
    for row in db[settings.marketplace_seller_products_collection].find({"status": "active"}, get_marketplace_projection()):
        product_rows.append(serialize_seller_product(row))
    for row in db[settings.normalized_collection].find(
        {"in_stock": True},
        {
            "_id": 0,
            "offer_id": 1,
            "title": 1,
            "link": 1,
            "source": 1,
            "image": 1,
            "images": 1,
            "price_pkr": 1,
            "shipping_pkr": 1,
            "rating": 1,
            "review_count": 1,
            "brand": 1,
            "model": 1,
            "category": 1,
            "subcategory": 1,
            "specifications": 1,
            "in_stock": 1,
            "last_scraped_dt": 1,
            "last_seen_at": 1,
        },
    ).limit(4000):
        product_rows.append(serialize_scraped_catalog_product(row))
    reviews = list(db[settings.marketplace_reviews_collection].find({}, {"_id": 0, "product_id": 1, "rating": 1}))
    orders = list(db[settings.marketplace_orders_collection].find({}, {"_id": 0}))
    interactions = list(db[settings.interactions_collection].find({}, {"_id": 0, "offer_id": 1, "event_type": 1, "event_ts": 1}))

    reviews_by_product: dict[str, list[int]] = {}
    for row in reviews:
        reviews_by_product.setdefault(str(row.get("product_id") or ""), []).append(int(row.get("rating") or 0))
    orders_by_product: dict[str, list[dict[str, Any]]] = {}
    for row in orders:
        orders_by_product.setdefault(str(row.get("product_id") or ""), []).append(row)
    interactions_by_product: dict[str, list[dict[str, Any]]] = {}
    for row in interactions:
        offer_id = str(row.get("offer_id") or "")
        interactions_by_product.setdefault(offer_id, []).append(row)

    feature_rows: list[dict[str, Any]] = []
    for product in product_rows:
        product_id = str(product.get("product_id") or "")
        app_ratings = reviews_by_product.get(product_id, [])
        app_rating = round(sum(app_ratings) / len(app_ratings), 2) if app_ratings else None
        app_review_count = len(app_ratings)
        source_rating = product.get("source_rating", product.get("rating"))
        source_review_count = int(product.get("source_review_count") or product.get("review_count") or 0)
        product_orders = orders_by_product.get(product_id, [])
        product_interactions = interactions_by_product.get(str(product.get("offer_id") or ""), [])
        feature_rows.append(
            build_marketplace_feature_row(
                product=product,
                app_rating=app_rating,
                app_review_count=app_review_count,
                source_rating=source_rating if isinstance(source_rating, (int, float)) else None,
                source_review_count=source_review_count,
                orders=product_orders,
                interactions=product_interactions,
            )
        )
    return feature_rows


def train_marketplace_model(
    settings: Settings,
    output_dir: str,
    *,
    epochs: int = 20,
    batch_size: int = 32,
    learning_rate: float = 1e-3,
    hidden_dim: int = 128,
) -> dict[str, Any]:
    rows = _gather_marketplace_training_rows(settings)
    if not rows:
        raise RuntimeError("No marketplace products available for marketplace DL training.")
    dataset = _MarketplaceDataset(rows)
    loader = DataLoader(dataset, batch_size=min(batch_size, len(dataset)), shuffle=True)
    model = MarketplaceMultiTaskNet(text_dim=TEXT_DIM, numeric_dim=NUMERIC_DIM, hidden_dim=hidden_dim)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    mse = nn.MSELoss()
    bce = nn.BCEWithLogitsLoss()
    model.train()
    last_loss = 0.0
    for _ in range(max(1, epochs)):
        for batch in loader:
            optimizer.zero_grad()
            out = model(batch)
            loss = (
                mse(out["rating"], batch["rating_target"])
                + mse(out["demand"], batch["demand_target"])
                + bce(out["month_logits"], batch["month_target"])
            )
            loss.backward()
            optimizer.step()
            last_loss = float(loss.detach().cpu())

    model_path = Path(output_dir)
    model_path.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), model_path / "marketplace_dl.pt")
    meta = {
        "text_dim": TEXT_DIM,
        "numeric_dim": NUMERIC_DIM,
        "hidden_dim": hidden_dim,
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "training_rows": len(rows),
        "final_loss": last_loss,
    }
    (model_path / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta
