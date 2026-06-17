from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
import math
from typing import Any

import numpy as np


TEXT_DIM = 64
NUMERIC_DIM = 12


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _hashed_index(value: Any, *, modulo: int) -> int:
    text = str(value or "").strip().lower()
    if not text:
        return 0
    return int(hashlib.sha1(text.encode("utf-8")).hexdigest(), 16) % modulo


def _hashed_text_vector(text: str, *, dim: int = TEXT_DIM) -> list[float]:
    vec = np.zeros(dim, dtype=np.float32)
    tokens = [token for token in str(text or "").lower().split() if token]
    if not tokens:
        return vec.tolist()
    for token in tokens:
        bucket = int(hashlib.sha1(token.encode("utf-8")).hexdigest(), 16) % dim
        vec[bucket] += 1.0
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec.tolist()


def _best_month_distribution(orders: list[dict[str, Any]], interactions: list[dict[str, Any]]) -> list[float]:
    monthly = defaultdict(float)
    for row in orders:
        created = row.get("created_at")
        if not isinstance(created, datetime):
            continue
        month = created.month
        monthly[month] += max(1.0, _safe_float(row.get("quantity")))
    for row in interactions:
        ts = row.get("event_ts")
        if not isinstance(ts, datetime):
            continue
        month = ts.month
        monthly[month] += 0.2 if str(row.get("event_type") or "") == "view" else 0.5
    out = np.zeros(12, dtype=np.float32)
    total = float(sum(monthly.values()))
    if total <= 0:
        return out.tolist()
    for month, value in monthly.items():
        if 1 <= month <= 12:
            out[month - 1] = float(value) / total
    return out.tolist()


def build_marketplace_feature_row(
    *,
    product: dict[str, Any],
    app_rating: float | None,
    app_review_count: int,
    source_rating: float | None,
    source_review_count: int,
    orders: list[dict[str, Any]],
    interactions: list[dict[str, Any]],
) -> dict[str, Any]:
    text = " ".join(
        [
            str(product.get("title") or ""),
            str(product.get("description") or ""),
            str(product.get("specifications") or ""),
            str(product.get("brand") or ""),
            str(product.get("model") or ""),
            str(product.get("category") or ""),
            str(product.get("subcategory") or ""),
        ]
    )
    units_sold = sum(max(1, _safe_int(row.get("quantity"))) for row in orders if str(row.get("status") or "") in {"paid", "fulfilled"})
    paid_orders = [row for row in orders if str(row.get("status") or "") in {"paid", "fulfilled"}]
    revenue = sum(_safe_float(row.get("total_pkr")) for row in paid_orders)
    event_counts = defaultdict(int)
    for row in interactions:
        event_counts[str(row.get("event_type") or "").lower()] += 1
    numeric = [
        _safe_float(product.get("price_pkr")),
        _safe_float(product.get("shipping_pkr")),
        1.0 if bool(product.get("in_stock", True)) else 0.0,
        float(_safe_int(product.get("stock_qty"))),
        float(app_review_count),
        float(source_review_count),
        float(app_rating if app_rating is not None else 0.0),
        float(source_rating if source_rating is not None else 0.0),
        float(event_counts.get("view", 0)),
        float(event_counts.get("click", 0) + event_counts.get("save", 0)),
        float(event_counts.get("purchase", 0)),
        float(units_sold),
    ]
    demand_target = float(units_sold) + float(event_counts.get("purchase", 0)) * 0.35 + float(event_counts.get("save", 0)) * 0.1
    rating_target = float(app_rating) if app_rating is not None else (float(source_rating) if source_rating is not None else 0.0)
    month_distribution = _best_month_distribution(paid_orders, interactions)
    seasonal_target = max(month_distribution) if month_distribution else 0.0
    return {
        "product_id": str(product.get("product_id") or ""),
        "offer_id": str(product.get("offer_id") or ""),
        "seller_id": _hashed_index(product.get("seller_id"), modulo=4096),
        "brand_id": _hashed_index(product.get("brand"), modulo=2048),
        "category_id": _hashed_index(product.get("category"), modulo=512),
        "subcategory_id": _hashed_index(product.get("subcategory"), modulo=1024),
        "text": _hashed_text_vector(text),
        "numeric": numeric,
        "targets": {
            "rating": rating_target,
            "demand": demand_target,
            "seasonal": seasonal_target,
            "month_distribution": month_distribution,
        },
        "stats": {
            "units_sold": units_sold,
            "revenue_pkr": round(revenue, 2),
            "order_count": len(paid_orders),
            "app_rating": app_rating,
            "app_review_count": app_review_count,
            "source_rating": source_rating,
            "source_review_count": source_review_count,
            "interaction_counts": dict(event_counts),
        },
    }


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))
