from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

import torch

from src.core.db import get_db
from src.core.marketplace import get_marketplace_projection, serialize_scraped_catalog_product, serialize_seller_product
from src.core.settings import Settings
from src.ml.marketplace_dl.features import NUMERIC_DIM, TEXT_DIM, build_marketplace_feature_row
from src.ml.marketplace_dl.model import MarketplaceMultiTaskNet


class MarketplacePredictor:
    def __init__(self, model_dir: str) -> None:
        self.model_dir = Path(model_dir)
        self.model = self._load_model()

    def _load_model(self) -> MarketplaceMultiTaskNet | None:
        metadata_path = self.model_dir / "metadata.json"
        weights_path = self.model_dir / "marketplace_dl.pt"
        if not metadata_path.exists() or not weights_path.exists():
            return None
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        model = MarketplaceMultiTaskNet(
            text_dim=int(metadata.get("text_dim", TEXT_DIM)),
            numeric_dim=int(metadata.get("numeric_dim", NUMERIC_DIM)),
            hidden_dim=int(metadata.get("hidden_dim", 128)),
        )
        model.load_state_dict(torch.load(weights_path, map_location="cpu"))
        model.eval()
        return model

    def available(self) -> bool:
        return self.model is not None

    def predict_row(self, feature_row: dict[str, Any]) -> dict[str, Any]:
        if self.model is None:
            return {
                "predicted_app_rating": None,
                "predicted_demand_score": None,
                "seasonal_relevance_score": None,
                "best_months": [],
                "best_month_labels": [],
                "prediction_confidence": "unavailable",
                "month_scores": [],
            }
        batch = {
            "text": torch.tensor([feature_row["text"]], dtype=torch.float32),
            "numeric": torch.tensor([feature_row["numeric"]], dtype=torch.float32),
            "seller_id": torch.tensor([feature_row["seller_id"]], dtype=torch.long),
            "brand_id": torch.tensor([feature_row["brand_id"]], dtype=torch.long),
            "category_id": torch.tensor([feature_row["category_id"]], dtype=torch.long),
            "subcategory_id": torch.tensor([feature_row["subcategory_id"]], dtype=torch.long),
        }
        with torch.no_grad():
            out = self.model(batch)
        rating = float(out["rating"][0].cpu())
        demand = float(out["demand"][0].cpu())
        month_probs = torch.sigmoid(out["month_logits"][0]).cpu().tolist()
        ranked = sorted(enumerate(month_probs, start=1), key=lambda x: x[1], reverse=True)
        best_months = [month for month, score in ranked[:3] if score >= 0.35]
        if not best_months:
            best_months = [ranked[0][0]] if ranked else []
        return {
            "predicted_app_rating": round(rating, 2),
            "predicted_demand_score": round(demand, 4),
            "seasonal_relevance_score": round(max(month_probs) if month_probs else 0.0, 4),
            "best_months": best_months,
            "best_month_labels": [str(month) for month in best_months],
            "prediction_confidence": "model",
            "month_scores": [round(float(value), 4) for value in month_probs],
        }


def persist_marketplace_predictions(settings: Settings, *, model_dir: str | None = None) -> dict[str, Any]:
    predictor = MarketplacePredictor(model_dir or settings.marketplace_dl_model_dir)
    db = get_db(settings)
    products: list[dict[str, Any]] = []
    for row in db[settings.marketplace_seller_products_collection].find({"status": "active"}, get_marketplace_projection()):
        products.append(serialize_seller_product(row))
    for row in db[settings.normalized_collection].find(
        {"in_stock": True},
        {"_id": 0, "offer_id": 1, "title": 1, "link": 1, "source": 1, "image": 1, "images": 1, "price_pkr": 1, "shipping_pkr": 1, "rating": 1, "review_count": 1, "brand": 1, "model": 1, "category": 1, "subcategory": 1, "specifications": 1, "in_stock": 1, "last_scraped_dt": 1, "last_seen_at": 1},
    ).limit(4000):
        products.append(serialize_scraped_catalog_product(row))
    reviews = list(db[settings.marketplace_reviews_collection].find({}, {"_id": 0, "product_id": 1, "rating": 1}))
    orders = list(db[settings.marketplace_orders_collection].find({}, {"_id": 0}))
    interactions = list(db[settings.interactions_collection].find({}, {"_id": 0, "offer_id": 1, "event_type": 1, "event_ts": 1}))
    review_map: dict[str, list[int]] = {}
    for row in reviews:
        review_map.setdefault(str(row.get("product_id") or ""), []).append(int(row.get("rating") or 0))
    orders_map: dict[str, list[dict[str, Any]]] = {}
    for row in orders:
        orders_map.setdefault(str(row.get("product_id") or ""), []).append(row)
    interactions_map: dict[str, list[dict[str, Any]]] = {}
    for row in interactions:
        interactions_map.setdefault(str(row.get("offer_id") or ""), []).append(row)
    updated = 0
    for product in products:
        product_id = str(product.get("product_id") or "")
        ratings = review_map.get(product_id, [])
        app_rating = round(sum(ratings) / len(ratings), 2) if ratings else None
        feature_row = build_marketplace_feature_row(
            product=product,
            app_rating=app_rating,
            app_review_count=len(ratings),
            source_rating=product.get("source_rating", product.get("rating")) if isinstance(product.get("source_rating", product.get("rating")), (int, float)) else None,
            source_review_count=int(product.get("source_review_count") or product.get("review_count") or 0),
            orders=orders_map.get(product_id, []),
            interactions=interactions_map.get(str(product.get("offer_id") or ""), []),
        )
        prediction = predictor.predict_row(feature_row)
        prediction_doc = {
            "product_id": product_id,
            "offer_id": str(product.get("offer_id") or ""),
            "listing_type": str(product.get("listing_type") or ""),
            "title": str(product.get("title") or ""),
            "updated_at": datetime.now(timezone.utc),
            **prediction,
        }
        db[settings.marketplace_predictions_collection].update_one(
            {"product_id": product_id},
            {"$set": prediction_doc},
            upsert=True,
        )
        updated += 1
    return {"updated": updated, "model_available": predictor.available()}
