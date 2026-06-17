from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import secrets
from typing import Any


ORDER_STATUSES = {"pending", "paid", "fulfilled", "cancelled"}
MONTH_NAMES = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}
SEASONAL_CATEGORY_HINTS = {
    "ac": {5, 6, 7, 8},
    "air conditioner": {5, 6, 7, 8},
    "cooler": {5, 6, 7, 8},
    "fan": {4, 5, 6, 7, 8},
    "heater": {11, 12, 1, 2},
    "blanket": {11, 12, 1, 2},
    "hoodie": {10, 11, 12, 1, 2},
    "lawn": {4, 5, 6, 7},
    "sunblock": {4, 5, 6, 7, 8},
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def build_order_doc(
    *,
    buyer: dict[str, Any],
    product: dict[str, Any],
    quantity: int,
    shipping_address: str | None,
    notes: str | None,
) -> dict[str, Any]:
    now = utcnow()
    unit_price = _safe_float(product.get("price_pkr"))
    shipping_pkr = _safe_float(product.get("shipping_pkr"))
    qty = max(1, int(quantity))
    return {
        "order_id": f"ord_{secrets.token_hex(12)}",
        "product_id": str(product.get("product_id") or ""),
        "offer_id": str(product.get("offer_id") or ""),
        "listing_type": str(product.get("listing_type") or ""),
        "buyer_id": str(buyer.get("user_id") or ""),
        "buyer_name": str(buyer.get("full_name") or "").strip() or "Buyer",
        "seller_id": str(product.get("seller_id") or ""),
        "seller_name": str(product.get("seller_name") or product.get("store_name") or product.get("source") or "").strip() or None,
        "store_name": str(product.get("store_name") or product.get("source") or "").strip() or None,
        "title": str(product.get("title") or "").strip(),
        "category": str(product.get("category") or "").strip() or None,
        "subcategory": str(product.get("subcategory") or "").strip() or None,
        "price_pkr": unit_price,
        "shipping_pkr": shipping_pkr,
        "quantity": qty,
        "subtotal_pkr": round(unit_price * qty, 2),
        "total_pkr": round(unit_price * qty + shipping_pkr, 2),
        "status": "pending",
        "shipping_address": str(shipping_address or "").strip() or None,
        "notes": str(notes or "").strip() or None,
        "created_at": now,
        "paid_at": None,
        "fulfilled_at": None,
        "updated_at": now,
    }


def serialize_order(doc: dict[str, Any]) -> dict[str, Any]:
    out = dict(doc)
    out.pop("_id", None)
    for key in ("created_at", "paid_at", "fulfilled_at", "updated_at"):
        value = out.get(key)
        if isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


def derive_prediction_defaults(item: dict[str, Any]) -> dict[str, Any]:
    title_blob = " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("category") or ""),
            str(item.get("subcategory") or ""),
            str(item.get("brand") or ""),
            str(item.get("model") or ""),
        ]
    ).lower()
    hinted_months: set[int] = set()
    for token, months in SEASONAL_CATEGORY_HINTS.items():
        if token in title_blob:
            hinted_months.update(months)
    best_months = sorted(hinted_months)[:4]
    source_rating = item.get("source_rating", item.get("rating"))
    app_rating = item.get("app_rating")
    rating_anchor = next((x for x in [app_rating, source_rating] if isinstance(x, (int, float))), None)
    predicted_rating = round(float(rating_anchor), 2) if rating_anchor is not None else None
    return {
        "predicted_app_rating": predicted_rating,
        "predicted_demand_score": None,
        "seasonal_relevance_score": None,
        "best_months": best_months,
        "best_month_labels": [MONTH_NAMES[m] for m in best_months],
        "prediction_confidence": "low" if not best_months and predicted_rating is None else "bootstrap",
    }


def summarize_orders(
    orders: list[dict[str, Any]],
    *,
    interactions: list[dict[str, Any]] | None = None,
    product_id: str | None = None,
    seller_id: str | None = None,
) -> dict[str, Any]:
    filtered = [
        row
        for row in orders
        if (not product_id or str(row.get("product_id") or "") == product_id)
        and (not seller_id or str(row.get("seller_id") or "") == seller_id)
    ]
    completed = [row for row in filtered if str(row.get("status") or "") in {"paid", "fulfilled"}]
    revenue = round(sum(_safe_float(row.get("total_pkr")) for row in completed), 2)
    units_sold = sum(_safe_int(row.get("quantity")) for row in completed)
    order_count = len(completed)
    monthly: dict[str, dict[str, Any]] = {}
    for row in completed:
        created = row.get("created_at")
        if not isinstance(created, datetime):
            continue
        bucket = created.astimezone(timezone.utc).strftime("%Y-%m")
        stats = monthly.setdefault(bucket, {"units_sold": 0, "revenue_pkr": 0.0, "order_count": 0})
        qty = _safe_int(row.get("quantity"))
        stats["units_sold"] += qty
        stats["revenue_pkr"] = round(float(stats["revenue_pkr"]) + _safe_float(row.get("total_pkr")), 2)
        stats["order_count"] += 1
    interaction_rows = interactions or []
    funnel = defaultdict(int)
    for row in interaction_rows:
        event_type = str(row.get("event_type") or "").strip().lower()
        if event_type:
            funnel[event_type] += 1
    return {
        "units_sold": units_sold,
        "revenue_pkr": revenue,
        "order_count": order_count,
        "monthly": [{"month": month, **stats} for month, stats in sorted(monthly.items())],
        "interaction_funnel": dict(funnel),
    }


def summarize_product_report(
    *,
    product: dict[str, Any],
    orders: list[dict[str, Any]],
    interactions: list[dict[str, Any]],
    prediction: dict[str, Any] | None,
) -> dict[str, Any]:
    sales = summarize_orders(orders, interactions=interactions, product_id=str(product.get("product_id") or ""))
    base_prediction = derive_prediction_defaults(product)
    if prediction:
        base_prediction.update(
            {
                "predicted_app_rating": prediction.get("predicted_app_rating", base_prediction.get("predicted_app_rating")),
                "predicted_demand_score": prediction.get("predicted_demand_score"),
                "seasonal_relevance_score": prediction.get("seasonal_relevance_score"),
                "best_months": list(prediction.get("best_months") or base_prediction.get("best_months") or []),
                "best_month_labels": list(prediction.get("best_month_labels") or base_prediction.get("best_month_labels") or []),
                "prediction_confidence": prediction.get("prediction_confidence") or base_prediction.get("prediction_confidence"),
            }
        )
    return {**sales, **base_prediction}


def summarize_seller_products(
    *,
    items: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    interactions: list[dict[str, Any]],
    predictions_by_product: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    by_product: list[dict[str, Any]] = []
    for item in items:
        product_id = str(item.get("product_id") or "")
        report = summarize_product_report(
            product=item,
            orders=orders,
            interactions=[row for row in interactions if str(row.get("product_id") or row.get("offer_id") or "") in {product_id, str(item.get("offer_id") or "")}],
            prediction=predictions_by_product.get(product_id),
        )
        by_product.append(
            {
                "product_id": product_id,
                "title": item.get("title"),
                "category": item.get("category"),
                "units_sold": report["units_sold"],
                "revenue_pkr": report["revenue_pkr"],
                "order_count": report["order_count"],
                "predicted_app_rating": report.get("predicted_app_rating"),
                "predicted_demand_score": report.get("predicted_demand_score"),
                "seasonal_relevance_score": report.get("seasonal_relevance_score"),
                "best_months": report.get("best_months") or [],
                "best_month_labels": report.get("best_month_labels") or [],
                "interaction_funnel": report.get("interaction_funnel") or {},
            }
        )
    totals = {
        "units_sold": sum(_safe_int(row.get("units_sold")) for row in by_product),
        "revenue_pkr": round(sum(_safe_float(row.get("revenue_pkr")) for row in by_product), 2),
        "order_count": sum(_safe_int(row.get("order_count")) for row in by_product),
        "product_count": len(by_product),
    }
    top_products = sorted(by_product, key=lambda row: (row.get("units_sold") or 0, row.get("revenue_pkr") or 0.0), reverse=True)
    return {
        "summary": totals,
        "products": top_products,
    }

