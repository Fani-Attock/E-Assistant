from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import re
import secrets
from typing import Any

import jwt

from src.core.normalize import normalize_image_gallery, normalize_text, primary_image_from_gallery
from src.core.settings import Settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(value: str) -> str:
    lowered = normalize_text(value or "")
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug[:80] or "item"


def _clean_text(value: Any, *, max_length: int = 4000) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:max_length]


def _normalize_string_list(values: Any, *, limit: int, item_limit: int) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _clean_text(value, max_length=item_limit)
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash or "$" not in password_hash:
        return False
    salt, expected = password_hash.split("$", 1)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()
    return hmac.compare_digest(actual, expected)


def issue_marketplace_token(user: dict[str, Any], settings: Settings) -> str:
    now = utcnow()
    payload = {
        "sub": str(user.get("user_id") or ""),
        "email": str(user.get("email") or ""),
        "role": str(user.get("role") or "buyer"),
        "type": "marketplace",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=max(1, settings.marketplace_token_ttl_hours))).timestamp()),
    }
    return jwt.encode(payload, settings.marketplace_jwt_secret, algorithm="HS256")


def decode_marketplace_token(token: str, settings: Settings) -> dict[str, Any]:
    payload = jwt.decode(token, settings.marketplace_jwt_secret, algorithms=["HS256"])
    if payload.get("type") != "marketplace":
        raise ValueError("invalid_token_type")
    sub = str(payload.get("sub") or "").strip()
    role = str(payload.get("role") or "").strip()
    if not sub or role not in {"buyer", "seller"}:
        raise ValueError("invalid_token_payload")
    return payload


def new_user_doc(*, full_name: str, email: str, password: str, role: str, store_name: str | None, bio: str | None) -> dict[str, Any]:
    now = utcnow()
    normalized_email = _clean_text(email, max_length=180).lower()
    normalized_role = "seller" if role == "merchant" else role
    role_value = normalized_role if normalized_role in {"buyer", "seller"} else "buyer"
    store = _clean_text(store_name or "", max_length=120)
    if role_value == "seller" and not store:
        store = _clean_text(full_name, max_length=120)
    user_id = f"mkt_{secrets.token_hex(12)}"
    return {
        "user_id": user_id,
        "full_name": _clean_text(full_name, max_length=120),
        "email": normalized_email,
        "password_hash": hash_password(password),
        "role": role_value,
        "store_name": store or None,
        "bio": _clean_text(bio or "", max_length=400) or None,
        "created_at": now,
        "updated_at": now,
    }


def serialize_marketplace_user(user: dict[str, Any]) -> dict[str, Any]:
    created_at = user.get("created_at")
    updated_at = user.get("updated_at")
    return {
        "user_id": str(user.get("user_id") or ""),
        "full_name": str(user.get("full_name") or ""),
        "email": str(user.get("email") or "").lower(),
        "role": str(user.get("role") or "buyer"),
        "store_name": str(user.get("store_name") or "").strip() or None,
        "bio": str(user.get("bio") or "").strip() or None,
        "created_at": created_at.isoformat() if isinstance(created_at, datetime) else None,
        "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else None,
    }


def build_seller_product_doc(
    *,
    seller: dict[str, Any],
    payload: dict[str, Any],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = utcnow()
    base = dict(existing or {})
    title = _clean_text(payload.get("title") if "title" in payload else base.get("title"), max_length=220)
    description = _clean_text(
        payload.get("description") if "description" in payload else base.get("description"),
        max_length=4000,
    )
    category = _clean_text(payload.get("category") if "category" in payload else base.get("category"), max_length=80)
    subcategory = _clean_text(
        payload.get("subcategory") if "subcategory" in payload else base.get("subcategory"),
        max_length=80,
    )
    brand = _clean_text(payload.get("brand") if "brand" in payload else base.get("brand"), max_length=80)
    model = _clean_text(payload.get("model") if "model" in payload else base.get("model"), max_length=80)
    specifications = _clean_text(
        payload.get("specifications") if "specifications" in payload else base.get("specifications"),
        max_length=4000,
    )
    external_url = _clean_text(
        payload.get("external_url") if "external_url" in payload else base.get("external_url"),
        max_length=1000,
    )
    images = _normalize_string_list(
        payload.get("images") if "images" in payload else base.get("images"),
        limit=8,
        item_limit=1000,
    )
    images = normalize_image_gallery(images)
    tags = _normalize_string_list(
        payload.get("tags") if "tags" in payload else base.get("tags"),
        limit=24,
        item_limit=80,
    )
    price_pkr = float(payload.get("price_pkr") if "price_pkr" in payload else base.get("price_pkr") or 0.0)
    shipping_pkr = float(payload.get("shipping_pkr") if "shipping_pkr" in payload else base.get("shipping_pkr") or 0.0)
    in_stock = bool(payload.get("in_stock") if "in_stock" in payload else base.get("in_stock", True))
    stock_qty = int(payload.get("stock_qty") if "stock_qty" in payload else base.get("stock_qty") or 0)

    product_id = str(base.get("product_id") or f"seller_{secrets.token_hex(12)}")
    offer_id = str(base.get("offer_id") or product_id)
    store_name = _clean_text(seller.get("store_name") or seller.get("full_name"), max_length=120)
    source = store_name or "Marketplace Seller"
    internal_path = f"/store/products/{product_id}"
    search_blob = " ".join(
        [
            title,
            description,
            specifications,
            category,
            subcategory,
            brand,
            model,
            " ".join(tags),
            source,
        ]
    )

    doc = {
        "product_id": product_id,
        "offer_id": offer_id,
        "seller_id": str(seller.get("user_id") or ""),
        "seller_name": _clean_text(seller.get("full_name"), max_length=120),
        "store_name": store_name or None,
        "title": title,
        "title_normalized": normalize_text(title),
        "description": description or None,
        "description_normalized": normalize_text(search_blob),
        "category": category or None,
        "subcategory": subcategory or None,
        "brand": brand or None,
        "model": model or None,
        "price_pkr": price_pkr,
        "shipping_pkr": shipping_pkr,
        "in_stock": in_stock,
        "stock_qty": max(0, stock_qty),
        "images": images,
        "image": primary_image_from_gallery(images),
        "specifications": specifications or None,
        "tags": tags,
        "external_url": external_url or None,
        "internal_path": internal_path,
        "source": source,
        "listing_type": "seller",
        "status": "active",
        "updated_at": now,
        "slug": _slugify(title),
    }
    if existing:
        doc["created_at"] = existing.get("created_at") or now
        doc["published_at"] = existing.get("published_at") or now
    else:
        doc["created_at"] = now
        doc["published_at"] = now
    return doc


def serialize_seller_product(doc: dict[str, Any]) -> dict[str, Any]:
    created_at = doc.get("created_at")
    updated_at = doc.get("updated_at")
    published_at = doc.get("published_at")
    price = float(doc.get("price_pkr") or 0.0)
    shipping = float(doc.get("shipping_pkr") or 0.0)
    return {
        "product_id": str(doc.get("product_id") or ""),
        "offer_id": str(doc.get("offer_id") or ""),
        "listing_type": "seller",
        "title": str(doc.get("title") or ""),
        "description": str(doc.get("description") or "").strip() or None,
        "category": str(doc.get("category") or "").strip() or None,
        "subcategory": str(doc.get("subcategory") or "").strip() or None,
        "brand": str(doc.get("brand") or "").strip() or None,
        "model": str(doc.get("model") or "").strip() or None,
        "price_pkr": price,
        "shipping_pkr": shipping,
        "total_price_pkr": price + shipping,
        "in_stock": bool(doc.get("in_stock", True)),
        "stock_qty": int(doc.get("stock_qty") or 0),
        "image": doc.get("image"),
        "images": list(doc.get("images") or []),
        "specifications": str(doc.get("specifications") or "").strip() or None,
        "tags": list(doc.get("tags") or []),
        "external_url": str(doc.get("external_url") or "").strip() or None,
        "internal_path": str(doc.get("internal_path") or "").strip() or None,
        "seller_id": str(doc.get("seller_id") or ""),
        "seller_name": str(doc.get("seller_name") or "").strip() or None,
        "store_name": str(doc.get("store_name") or "").strip() or None,
        "source": str(doc.get("source") or "Marketplace Seller"),
        "source_label": str(doc.get("store_name") or doc.get("source") or "Marketplace Seller"),
        "status": str(doc.get("status") or "active"),
        "created_at": created_at.isoformat() if isinstance(created_at, datetime) else None,
        "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else None,
        "published_at": published_at.isoformat() if isinstance(published_at, datetime) else None,
        "rating": None,
        "review_count": 0,
        "link": str(doc.get("external_url") or doc.get("internal_path") or "").strip() or None,
    }


def serialize_scraped_catalog_product(doc: dict[str, Any]) -> dict[str, Any]:
    price = doc.get("price_pkr")
    shipping = doc.get("shipping_pkr") or 0.0
    total = None if price in (None, "") else float(price) + float(shipping)
    product_id = f"scraped_{doc.get('offer_id') or _slugify(str(doc.get('link') or doc.get('title') or 'item'))}"
    updated_at = doc.get("last_scraped_dt") or doc.get("last_seen_at")
    updated_iso = updated_at.isoformat() if isinstance(updated_at, datetime) else None
    link = str(doc.get("link") or "").strip() or None
    source = str(doc.get("source") or "scraped")
    return {
        "product_id": product_id,
        "offer_id": str(doc.get("offer_id") or ""),
        "listing_type": "scraped",
        "title": str(doc.get("title") or ""),
        "description": str(doc.get("specifications") or "").strip() or None,
        "category": str(doc.get("category") or "").strip() or None,
        "subcategory": str(doc.get("subcategory") or "").strip() or None,
        "brand": str(doc.get("brand") or "").strip() or None,
        "model": str(doc.get("model") or "").strip() or None,
        "price_pkr": float(price) if price not in (None, "") else None,
        "shipping_pkr": float(shipping),
        "total_price_pkr": total,
        "in_stock": bool(doc.get("in_stock", True)),
        "stock_qty": None,
        "image": doc.get("image"),
        "images": normalize_image_gallery(doc.get("images") or doc.get("image")),
        "specifications": str(doc.get("specifications") or "").strip() or None,
        "tags": [],
        "external_url": link,
        "internal_path": None,
        "seller_id": None,
        "seller_name": None,
        "store_name": None,
        "source": source,
        "source_label": source.upper(),
        "status": "active",
        "created_at": updated_iso,
        "updated_at": updated_iso,
        "published_at": updated_iso,
        "rating": float(doc.get("rating")) if doc.get("rating") not in (None, "") else None,
        "review_count": int(doc.get("review_count") or 0),
        "link": link,
    }


def seller_product_to_search_doc(doc: dict[str, Any]) -> dict[str, Any]:
    data = serialize_seller_product(doc)
    return {
        "offer_id": data["offer_id"],
        "product_id": data["product_id"],
        "title": data["title"],
        "title_normalized": normalize_text(data["title"]),
        "link": data["link"] or data["internal_path"],
        "source": data["source"],
        "image": data["image"],
        "images": list(data.get("images") or []),
        "price_pkr": data["price_pkr"],
        "shipping_pkr": data["shipping_pkr"],
        "total_price_pkr": data["total_price_pkr"],
        "specifications": data["specifications"] or data["description"] or "",
        "brand": data["brand"],
        "model": data["model"],
        "category": data["category"],
        "subcategory": data["subcategory"],
        "storage_gb": None,
        "ram_gb": None,
        "in_stock": data["in_stock"],
        "rating": None,
        "review_count": 0,
        "last_scraped": data["updated_at"],
        "last_scraped_dt": doc.get("updated_at"),
        "last_seen_at": doc.get("updated_at"),
        "listing_type": "seller",
        "seller_id": data["seller_id"],
        "seller_name": data["seller_name"],
        "store_name": data["store_name"],
        "internal_path": data["internal_path"],
        "external_url": data["external_url"],
        "description": data["description"],
    }


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if parsed == parsed else None
    except (TypeError, ValueError):
        return None


def product_family_key(item: dict[str, Any]) -> str:
    brand = normalize_text(str(item.get("brand") or ""))
    model = normalize_text(str(item.get("model") or ""))
    category = normalize_text(str(item.get("category") or ""))
    title = normalize_text(str(item.get("title") or ""))
    if brand and model:
        seed = f"{brand}|{model}|{category}"
    else:
        trimmed_title = " ".join(title.split()[:8])
        seed = f"{brand}|{trimmed_title}|{category}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def attach_price_ranges(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        return []
    grouped_prices: dict[str, list[float]] = {}
    family_keys: list[str] = []
    for item in items:
        family_key = product_family_key(item)
        family_keys.append(family_key)
        price = _safe_float(item.get("total_price_pkr"))
        if price is None:
            price = _safe_float(item.get("price_pkr"))
        if price is None:
            continue
        grouped_prices.setdefault(family_key, []).append(price)

    enriched: list[dict[str, Any]] = []
    for item, family_key in zip(items, family_keys):
        prices = grouped_prices.get(family_key) or []
        merged = dict(item)
        merged["product_family_key"] = family_key
        if prices:
            merged["price_range_pkr_min"] = round(min(prices), 2)
            merged["price_range_pkr_max"] = round(max(prices), 2)
        else:
            merged["price_range_pkr_min"] = None
            merged["price_range_pkr_max"] = None
        enriched.append(merged)
    return enriched


def apply_display_source_fields(item: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(item)
    source_rating = enriched.get("source_rating", enriched.get("rating"))
    source_review_count = enriched.get("source_review_count", enriched.get("review_count"))
    predicted_rating = enriched.get("predicted_app_rating")
    if isinstance(source_rating, (int, float)):
        enriched["display_source_rating"] = float(source_rating)
        enriched["display_source_review_count"] = int(source_review_count or 0)
        enriched["display_source_rating_kind"] = "scraped"
    elif isinstance(predicted_rating, (int, float)):
        enriched["display_source_rating"] = float(predicted_rating)
        enriched["display_source_review_count"] = int(source_review_count or 0) if source_review_count not in (None, "") else None
        enriched["display_source_rating_kind"] = "predicted"
    else:
        enriched["display_source_rating"] = None
        enriched["display_source_review_count"] = int(source_review_count or 0) if source_review_count not in (None, "") else None
        enriched["display_source_rating_kind"] = "missing"
    return enriched


def attach_display_source_fields(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [apply_display_source_fields(item) for item in items]


def get_marketplace_projection() -> dict[str, int]:
    return {
        "_id": 0,
        "product_id": 1,
        "offer_id": 1,
        "seller_id": 1,
        "seller_name": 1,
        "store_name": 1,
        "title": 1,
        "title_normalized": 1,
        "description": 1,
        "description_normalized": 1,
        "category": 1,
        "subcategory": 1,
        "brand": 1,
        "model": 1,
        "price_pkr": 1,
        "shipping_pkr": 1,
        "in_stock": 1,
        "stock_qty": 1,
        "images": 1,
        "image": 1,
        "specifications": 1,
        "tags": 1,
        "external_url": 1,
        "internal_path": 1,
        "source": 1,
        "listing_type": 1,
        "status": 1,
        "created_at": 1,
        "updated_at": 1,
        "published_at": 1,
    }


def lookup_store_product_offer(*, db, settings: Settings, product_id: str) -> dict[str, Any] | None:
    def _collection_names() -> set[str]:
        if hasattr(db, "list_collection_names"):
            try:
                return set(db.list_collection_names())
            except Exception:
                return set()
        try:
            return set(db.keys())
        except Exception:
            return set()

    def _prediction_doc(normalized_id: str) -> dict[str, Any] | None:
        collection_names = _collection_names()
        configured_name = settings.marketplace_predictions_collection
        if configured_name in collection_names:
            return db[configured_name].find_one({"product_id": normalized_id}, {"_id": 0})
        legacy_name = "marketplace_predictions"
        if legacy_name in collection_names:
            return db[legacy_name].find_one({"product_id": normalized_id}, {"_id": 0})
        return None

    def _attach_runtime_fields(row: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(row)
        collection_names = _collection_names()
        product_reviews: list[dict[str, Any]] = []
        if settings.marketplace_reviews_collection in collection_names:
            product_reviews = list(
                db[settings.marketplace_reviews_collection].find(
                    {"product_id": str(enriched.get("product_id") or "")},
                    {"_id": 0, "rating": 1},
                )
            )
        ratings = [int(review.get("rating") or 0) for review in product_reviews if int(review.get("rating") or 0) > 0]
        enriched["app_review_count"] = len(ratings)
        enriched["app_rating"] = round(sum(ratings) / len(ratings), 2) if ratings else None

        prediction = _prediction_doc(str(enriched.get("product_id") or ""))
        if prediction:
            enriched["predicted_app_rating"] = prediction.get("predicted_app_rating")
            enriched["predicted_demand_score"] = prediction.get("predicted_demand_score")
            enriched["seasonal_relevance_score"] = prediction.get("seasonal_relevance_score")
            enriched["best_months"] = prediction.get("best_months") or []
            enriched["best_month_labels"] = prediction.get("best_month_labels") or []
            enriched["prediction_confidence"] = prediction.get("prediction_confidence")

        universe: list[dict[str, Any]] = []
        if settings.marketplace_seller_products_collection in collection_names:
            seller_collection = db[settings.marketplace_seller_products_collection]
            if hasattr(seller_collection, "find"):
                for seller_row in seller_collection.find(
                    {"status": "active"},
                    get_marketplace_projection(),
                ).limit(4000):
                    universe.append(seller_product_to_search_doc(seller_row))
        if settings.normalized_collection in collection_names:
            normalized_collection = db[settings.normalized_collection]
            if hasattr(normalized_collection, "find"):
                for scraped_row in normalized_collection.find(
                    {"in_stock": True},
                    {"_id": 0},
                ).limit(4000):
                    normalized_offer_id = str(scraped_row.get("offer_id") or "")
                    merged_row = dict(scraped_row)
                    merged_row["product_id"] = f"scraped_{normalized_offer_id}"
                    merged_row["listing_type"] = "scraped"
                    merged_row["internal_path"] = None
                    merged_row["external_url"] = str(merged_row.get("link") or "").strip() or None
                    universe.append(merged_row)
        universe.append(enriched)
        ranged = attach_price_ranges(universe)
        family_key = str(product_family_key(enriched))
        for candidate in ranged:
            if str(candidate.get("product_id") or "") == str(enriched.get("product_id") or ""):
                enriched["price_range_pkr_min"] = candidate.get("price_range_pkr_min")
                enriched["price_range_pkr_max"] = candidate.get("price_range_pkr_max")
                break
        if enriched.get("price_range_pkr_min") is None or enriched.get("price_range_pkr_max") is None:
            for candidate in ranged:
                if str(candidate.get("product_family_key") or "") == family_key:
                    enriched["price_range_pkr_min"] = candidate.get("price_range_pkr_min")
                    enriched["price_range_pkr_max"] = candidate.get("price_range_pkr_max")
                    break
        return apply_display_source_fields(enriched)

    normalized_product_id = str(product_id or "").strip()
    if not normalized_product_id:
        return None
    if normalized_product_id.startswith("seller_"):
        doc = db[settings.marketplace_seller_products_collection].find_one(
            {"product_id": normalized_product_id, "status": "active"},
            get_marketplace_projection(),
        )
        return _attach_runtime_fields(seller_product_to_search_doc(doc)) if doc else None

    offer_id = normalized_product_id.removeprefix("scraped_")
    doc = db[settings.normalized_collection].find_one({"offer_id": offer_id}, {"_id": 0})
    if not doc:
        return None
    row = dict(doc)
    row["product_id"] = normalized_product_id if normalized_product_id.startswith("scraped_") else f"scraped_{offer_id}"
    row["listing_type"] = "scraped"
    row["internal_path"] = None
    row["external_url"] = str(row.get("link") or "").strip() or None
    return _attach_runtime_fields(row)
