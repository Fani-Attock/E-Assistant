from datetime import datetime, timezone
from pathlib import Path
import re
import time
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pymongo import MongoClient

from src.agent.assistant import AssistantAgent
from src.core.auth import decode_jwt_token
from src.core.audit import write_audit_log
from src.core.db import ensure_indexes
from src.core.interaction_store import log_interaction
from src.core.llm_query_parser import parse_query_with_llm
from src.core.logging_utils import setup_logging
from src.core.marketplace import (
    attach_display_source_fields,
    attach_price_ranges,
    build_seller_product_doc,
    decode_marketplace_token,
    get_marketplace_projection,
    issue_marketplace_token,
    new_user_doc,
    serialize_marketplace_user,
    serialize_scraped_catalog_product,
    serialize_seller_product,
    verify_password,
)
from src.core.marketplace_analytics import (
    MONTH_NAMES,
    ORDER_STATUSES,
    build_order_doc,
    derive_prediction_defaults,
    serialize_order,
    summarize_product_report,
    summarize_seller_products,
)
from src.core.normalize import normalize_search_query, normalize_text
from src.core.report_store import get_report, list_reports, save_report
from src.core.rate_limit import InMemoryRateLimiter, RedisRateLimiter
from src.core.schemas import (
    AssistantRequest,
    InteractionIn,
    MarketplaceLoginRequest,
    MarketplaceOrderIn,
    MarketplaceOrderStatusUpdate,
    MarketplacePredictionTrainRequest,
    MarketplaceProductReviewIn,
    MarketplaceRegisterRequest,
    MarketplaceSellerProductIn,
    MarketplaceSellerProductUpdate,
)
from src.core.search_pipeline import SearchPipeline
from src.core.settings import Settings
from src.ml.marketplace_dl.infer import persist_marketplace_predictions
from src.ml.marketplace_dl.train import train_marketplace_model

REQUEST_COUNT = Counter("api_requests_total", "Total API requests", ["method", "path", "status"])
REQUEST_LATENCY = Histogram("api_request_latency_seconds", "API latency in seconds", ["method", "path"])


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    prefix = "bearer "
    if authorization.lower().startswith(prefix):
        return authorization[len(prefix) :].strip()
    return None


def _build_rate_limiter(settings: Settings, logger):
    if settings.rate_limit_backend == "redis":
        try:
            limiter = RedisRateLimiter(
                redis_url=settings.redis_url,
                max_requests=settings.rate_limit_requests,
                window_seconds=settings.rate_limit_window_seconds,
            )
            logger.info("Using redis rate limiter backend")
            return limiter
        except Exception:
            logger.exception("Redis rate limiter unavailable; falling back to in-memory")
    return InMemoryRateLimiter(
        max_requests=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )


def _runtime(app: FastAPI) -> dict[str, Any]:
    return {
        "settings": app.state.settings,
        "pipeline": app.state.pipeline,
        "app_db": app.state.app_db,
        "assistant_agent": app.state.assistant_agent,
        "rate_limiter": app.state.rate_limiter,
        "logger": app.state.logger,
    }


def require_scope(scope: str):
    def _dependency(
        request: Request,
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
        x_admin_api_key: str | None = Header(default=None),
    ) -> None:
        settings: Settings = request.app.state.settings
        if settings.auth_mode == "jwt":
            if not settings.jwt_secret:
                raise HTTPException(status_code=500, detail="JWT_SECRET_not_configured")
            token = _extract_bearer_token(authorization)
            if not token:
                raise HTTPException(status_code=401, detail="missing_bearer_token")
            try:
                principal = decode_jwt_token(token, settings)
            except Exception as exc:
                raise HTTPException(status_code=401, detail=f"invalid_token:{exc}") from exc
            allowed = ("admin" in principal.scopes) or (scope in principal.scopes)
            if not allowed:
                raise HTTPException(status_code=403, detail="insufficient_scope")
            return

        if scope == "admin":
            if not settings.require_auth_for_write and not settings.admin_api_key:
                return
            expected = settings.admin_api_key
            if not expected:
                raise HTTPException(status_code=500, detail="ADMIN_API_KEY_not_configured")
            if x_admin_api_key == expected:
                return
            raise HTTPException(status_code=401, detail="admin_unauthorized")

        if not settings.require_auth_for_write:
            return
        service_ok = bool(settings.service_api_key and x_api_key == settings.service_api_key)
        admin_ok = bool(settings.admin_api_key and x_admin_api_key == settings.admin_api_key)
        if service_ok or admin_ok:
            return
        raise HTTPException(status_code=401, detail="unauthorized")

    return _dependency


def _marketplace_users(request: Request):
    return request.app.state.app_db[request.app.state.settings.marketplace_users_collection]


def _marketplace_products(request: Request):
    return request.app.state.app_db[request.app.state.settings.marketplace_seller_products_collection]


def _marketplace_reviews(request: Request):
    return request.app.state.app_db[request.app.state.settings.marketplace_reviews_collection]


def _marketplace_orders(request: Request):
    return request.app.state.app_db[request.app.state.settings.marketplace_orders_collection]


def _marketplace_predictions(request: Request):
    configured_name = request.app.state.settings.marketplace_predictions_collection
    db = request.app.state.app_db
    if hasattr(db, "list_collection_names"):
        collection_names = set(db.list_collection_names())
    else:
        try:
            collection_names = set(db.keys())
        except Exception:
            collection_names = set()
    if configured_name in collection_names:
        return db[configured_name]
    legacy_name = "marketplace_predictions"
    if legacy_name in collection_names:
        return db[legacy_name]
    return db[configured_name]


def _validation_error_payload(exc: RequestValidationError) -> dict[str, Any]:
    field_errors: list[dict[str, Any]] = []
    for item in exc.errors():
        loc = [str(part) for part in item.get("loc", []) if str(part) not in {"body", "query", "path"}]
        field_errors.append(
            {
                "field": ".".join(loc) if loc else "request",
                "message": str(item.get("msg") or "Invalid value"),
                "type": str(item.get("type") or "validation_error"),
            }
        )
    message = field_errors[0]["message"] if field_errors else "Validation failed."
    return {
        "message": message,
        "field_errors": field_errors,
        "raw_detail": exc.errors(),
    }


def require_marketplace_user(*roles: str):
    allowed_roles = set(roles)

    def _dependency(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        token = _extract_bearer_token(authorization)
        if not token:
            raise HTTPException(status_code=401, detail="missing_marketplace_token")
        try:
            payload = decode_marketplace_token(token, request.app.state.settings)
        except Exception as exc:
            raise HTTPException(status_code=401, detail=f"invalid_marketplace_token:{exc}") from exc
        user = _marketplace_users(request).find_one({"user_id": payload["sub"]}, {"_id": 0, "password_hash": 0})
        if not user:
            raise HTTPException(status_code=401, detail="marketplace_user_not_found")
        role = str(user.get("role") or "")
        if allowed_roles and role not in allowed_roles:
            raise HTTPException(status_code=403, detail="marketplace_role_forbidden")
        return user

    return _dependency


def _optional_marketplace_user(request: Request, authorization: str | None) -> dict[str, Any] | None:
    token = _extract_bearer_token(authorization)
    if not token:
        return None
    try:
        payload = decode_marketplace_token(token, request.app.state.settings)
    except Exception:
        return None
    return _marketplace_users(request).find_one({"user_id": payload["sub"]}, {"_id": 0, "password_hash": 0})


def _clean_review_text(value: Any, *, max_length: int) -> str | None:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return None
    return text[:max_length]


def _empty_app_rating_summary() -> dict[str, Any]:
    return {
        "average_rating": None,
        "review_count": 0,
        "breakdown": {5: 0, 4: 0, 3: 0, 2: 0, 1: 0},
    }


def _summarize_app_reviews(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return _empty_app_rating_summary()
    breakdown = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
    total = 0
    count = 0
    for row in rows:
        try:
            rating = int(row.get("rating") or 0)
        except (TypeError, ValueError):
            rating = 0
        if rating < 1 or rating > 5:
            continue
        breakdown[rating] += 1
        total += rating
        count += 1
    if count == 0:
        return _empty_app_rating_summary()
    return {
        "average_rating": round(total / count, 2),
        "review_count": count,
        "breakdown": breakdown,
    }


def _serialize_marketplace_review(row: dict[str, Any]) -> dict[str, Any]:
    created_at = row.get("created_at")
    updated_at = row.get("updated_at")
    return {
        "review_id": str(row.get("review_id") or ""),
        "product_id": str(row.get("product_id") or ""),
        "offer_id": str(row.get("offer_id") or ""),
        "listing_type": str(row.get("listing_type") or ""),
        "user_id": str(row.get("user_id") or ""),
        "user_name": str(row.get("user_name") or "").strip() or None,
        "user_role": str(row.get("user_role") or "").strip() or None,
        "rating": int(row.get("rating") or 0),
        "title": str(row.get("title") or "").strip() or None,
        "body": str(row.get("body") or "").strip() or None,
        "created_at": created_at.isoformat() if isinstance(created_at, datetime) else None,
        "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else None,
    }


def _apply_rating_fields(item: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(item)
    enriched["source_rating"] = enriched.get("rating")
    enriched["source_review_count"] = enriched.get("review_count")
    enriched["app_rating"] = summary.get("average_rating")
    enriched["app_review_count"] = summary.get("review_count", 0)
    enriched["app_rating_breakdown"] = dict(summary.get("breakdown") or {})
    return enriched


def _attach_app_rating_summaries(request: Request, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        return []
    product_ids = [str(item.get("product_id") or "").strip() for item in items if str(item.get("product_id") or "").strip()]
    if not product_ids:
        return [_apply_rating_fields(item, _empty_app_rating_summary()) for item in items]
    review_rows = list(
        _marketplace_reviews(request).find(
            {"product_id": {"$in": product_ids}},
            {
                "_id": 0,
                "product_id": 1,
                "rating": 1,
            },
        )
    )
    grouped: dict[str, list[dict[str, Any]]] = {product_id: [] for product_id in product_ids}
    for row in review_rows:
        product_id = str(row.get("product_id") or "").strip()
        if product_id:
            grouped.setdefault(product_id, []).append(row)
    return [_apply_rating_fields(item, _summarize_app_reviews(grouped.get(str(item.get("product_id") or ""), []))) for item in items]


def _prediction_map(request: Request, product_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not product_ids:
        return {}
    rows = list(
        _marketplace_predictions(request).find(
            {"product_id": {"$in": product_ids}},
            {"_id": 0},
        )
    )
    return {str(row.get("product_id") or ""): row for row in rows if str(row.get("product_id") or "").strip()}


def _interaction_rows_for_offer_ids(request: Request, offer_ids: list[str]) -> list[dict[str, Any]]:
    if not offer_ids:
        return []
    return list(
        request.app.state.app_db[request.app.state.settings.interactions_collection].find(
            {"offer_id": {"$in": offer_ids}},
            {"_id": 0, "offer_id": 1, "event_type": 1, "event_ts": 1},
        )
    )


def _order_rows_for_products(request: Request, product_ids: list[str]) -> list[dict[str, Any]]:
    if not product_ids:
        return []
    return list(_marketplace_orders(request).find({"product_id": {"$in": product_ids}}, {"_id": 0}))


def _attach_sales_prediction_fields(request: Request, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        return []
    product_ids = [str(item.get("product_id") or "").strip() for item in items if str(item.get("product_id") or "").strip()]
    offer_ids = [str(item.get("offer_id") or "").strip() for item in items if str(item.get("offer_id") or "").strip()]
    predictions = _prediction_map(request, product_ids)
    orders = _order_rows_for_products(request, product_ids)
    interactions = _interaction_rows_for_offer_ids(request, offer_ids)
    orders_by_product: dict[str, list[dict[str, Any]]] = {}
    interactions_by_offer: dict[str, list[dict[str, Any]]] = {}
    for row in orders:
        orders_by_product.setdefault(str(row.get("product_id") or ""), []).append(row)
    for row in interactions:
        interactions_by_offer.setdefault(str(row.get("offer_id") or ""), []).append(row)

    enriched: list[dict[str, Any]] = []
    for item in items:
        product_id = str(item.get("product_id") or "")
        offer_id = str(item.get("offer_id") or "")
        prediction = predictions.get(product_id)
        report = summarize_product_report(
            product=item,
            orders=orders_by_product.get(product_id, []),
            interactions=interactions_by_offer.get(offer_id, []),
            prediction=prediction,
        )
        merged = dict(item)
        merged["units_sold"] = report.get("units_sold", 0)
        merged["order_count"] = report.get("order_count", 0)
        merged["revenue_pkr"] = report.get("revenue_pkr", 0.0)
        merged["interaction_funnel"] = report.get("interaction_funnel") or {}
        merged["predicted_app_rating"] = report.get("predicted_app_rating")
        merged["predicted_demand_score"] = report.get("predicted_demand_score")
        merged["seasonal_relevance_score"] = report.get("seasonal_relevance_score")
        merged["best_months"] = report.get("best_months") or []
        merged["best_month_labels"] = report.get("best_month_labels") or [MONTH_NAMES.get(month, str(month)) for month in report.get("best_months") or []]
        merged["prediction_confidence"] = report.get("prediction_confidence")
        enriched.append(merged)
    return enriched


def _finalize_store_display(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return attach_display_source_fields(attach_price_ranges(items))


def _collapse_catalog_families(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    collapsed: list[dict[str, Any]] = []
    for item in items:
        family_key = str(item.get("product_family_key") or "")
        dedupe_key = family_key or str(item.get("product_id") or "")
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        collapsed.append(item)
    return collapsed


def _store_universe_rows(request: Request) -> list[dict[str, Any]]:
    settings = request.app.state.settings
    db = request.app.state.app_db
    scraped_rows = [
        serialize_scraped_catalog_product(row)
        for row in db[settings.normalized_collection].find({"in_stock": True}, _scraped_catalog_projection()).limit(4000)
    ]
    seller_rows = [
        serialize_seller_product(row)
        for row in db[settings.marketplace_seller_products_collection].find({"status": "active"}, get_marketplace_projection()).limit(4000)
    ]
    return scraped_rows + seller_rows


def _apply_store_family_range(request: Request, item: dict[str, Any]) -> dict[str, Any]:
    universe = _finalize_store_display(_attach_sales_prediction_fields(request, _attach_app_rating_summaries(request, _store_universe_rows(request))))
    family_key = str(item.get("product_family_key") or "")
    if not family_key:
        return item
    for row in universe:
        if str(row.get("product_id") or "") == str(item.get("product_id") or ""):
            return row
    matching = [row for row in universe if str(row.get("product_family_key") or "") == family_key]
    if not matching:
        return item
    return dict(item, price_range_pkr_min=matching[0].get("price_range_pkr_min"), price_range_pkr_max=matching[0].get("price_range_pkr_max"))


def _build_review_doc(*, product: dict[str, Any], user: dict[str, Any], payload: MarketplaceProductReviewIn, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    review_id = str(existing.get("review_id") or "") if existing else ""
    if not review_id:
        review_id = f"review_{product['product_id']}_{user['user_id']}"
    return {
        "review_id": review_id,
        "product_id": str(product.get("product_id") or ""),
        "offer_id": str(product.get("offer_id") or ""),
        "listing_type": str(product.get("listing_type") or ""),
        "seller_id": str(product.get("seller_id") or "").strip() or None,
        "product_title": str(product.get("title") or "").strip(),
        "user_id": str(user.get("user_id") or ""),
        "user_name": str(user.get("store_name") or user.get("full_name") or "").strip() or "Marketplace User",
        "user_role": str(user.get("role") or "").strip() or None,
        "rating": int(payload.rating),
        "title": _clean_review_text(payload.title, max_length=160),
        "body": _clean_review_text(payload.body, max_length=2000),
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": now,
    }


def _scraped_catalog_projection() -> dict[str, int]:
    return {
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
    }


def _catalog_match_score(item: dict[str, Any], normalized_query: str) -> float:
    if not normalized_query:
        return 0.0
    tokens = [token for token in normalized_query.split() if token]
    if not tokens:
        return 0.0
    haystack = normalize_text(
        " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("brand") or ""),
                str(item.get("model") or ""),
                str(item.get("description") or item.get("specifications") or ""),
                str(item.get("category") or ""),
                str(item.get("subcategory") or ""),
                str(item.get("source_label") or item.get("source") or ""),
            ]
        )
    )
    if not haystack:
        return 0.0
    score = 0.0
    for token in tokens:
        if token in haystack:
            score += 1.0
    if normalized_query in haystack:
        score += 2.5
    return score


def _catalog_updated_at(item: dict[str, Any]) -> float:
    for key in ("updated_at", "published_at", "created_at"):
        value = item.get(key)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
    return 0.0


def _matches_category(item: dict[str, Any], category: str) -> bool:
    wanted = normalize_text(category)
    if not wanted:
        return True
    fields = [item.get("category"), item.get("subcategory")]
    return any(wanted in normalize_text(str(value or "")) for value in fields)


def _list_store_catalog(
    *,
    request: Request,
    query: str,
    category: str | None,
    listing_type: str,
    seller_id: str | None,
    min_price: float | None,
    max_price: float | None,
    sort: str,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    settings = request.app.state.settings
    db = request.app.state.app_db
    normalized_query = normalize_search_query(query) if query else ""
    token_regex = None
    if normalized_query:
        token_regex = {"$regex": ".*".join(re.escape(token) for token in normalized_query.split()[:8]), "$options": "i"}

    scraped_rows: list[dict[str, Any]] = []
    if listing_type in {"all", "scraped"}:
        scraped_filter: dict[str, Any] = {"in_stock": True}
        if token_regex is not None:
            scraped_filter["$or"] = [
                {"title_normalized": token_regex},
                {"brand": token_regex},
                {"model": token_regex},
                {"specifications": token_regex},
            ]
        scraped_rows = [
            serialize_scraped_catalog_product(row)
            for row in db[settings.normalized_collection].find(scraped_filter, _scraped_catalog_projection()).limit(3000)
        ]

    seller_rows: list[dict[str, Any]] = []
    if listing_type in {"all", "seller"}:
        seller_filter: dict[str, Any] = {"status": "active"}
        if seller_id:
            seller_filter["seller_id"] = seller_id
        if token_regex is not None:
            seller_filter["$or"] = [
                {"title_normalized": token_regex},
                {"brand": token_regex},
                {"model": token_regex},
                {"description_normalized": token_regex},
            ]
        seller_rows = [
            serialize_seller_product(row)
            for row in db[settings.marketplace_seller_products_collection].find(seller_filter, get_marketplace_projection()).limit(3000)
        ]

    merged = scraped_rows + seller_rows
    if category:
        merged = [row for row in merged if _matches_category(row, category)]
    if min_price is not None:
        merged = [row for row in merged if row.get("price_pkr") is not None and float(row["price_pkr"]) >= min_price]
    if max_price is not None:
        merged = [row for row in merged if row.get("price_pkr") is not None and float(row["price_pkr"]) <= max_price]

    if normalized_query:
        merged = [row for row in merged if _catalog_match_score(row, normalized_query) > 0]

    if sort == "price_asc":
        merged.sort(key=lambda row: (float(row.get("total_price_pkr") or 1e18), row.get("title") or ""))
    elif sort == "price_desc":
        merged.sort(key=lambda row: (float(row.get("total_price_pkr") or -1.0)), reverse=True)
    elif sort == "rating":
        merged.sort(key=lambda row: (float(row.get("rating") or -1.0), int(row.get("review_count") or 0)), reverse=True)
    elif sort == "relevance" and normalized_query:
        merged.sort(
            key=lambda row: (
                _catalog_match_score(row, normalized_query),
                float(row.get("rating") or -1.0),
                -float(row.get("total_price_pkr") or 1e18),
            ),
            reverse=True,
        )
    else:
        merged.sort(
            key=lambda row: (
                _catalog_updated_at(row),
                float(row.get("rating") or -1.0),
                -float(row.get("total_price_pkr") or 1e18),
            ),
            reverse=True,
        )

    merged = _collapse_catalog_families(
        _finalize_store_display(
            _attach_sales_prediction_fields(request, _attach_app_rating_summaries(request, merged))
        )
    )
    total = len(merged)
    start = max(0, (page - 1) * page_size)
    end = start + page_size
    page_items = merged[start:end]
    categories = sorted(
        {
            str(row.get("category") or "").strip()
            for row in merged
            if str(row.get("category") or "").strip()
        }
    )
    return {
        "query": query,
        "category": category,
        "listing_type": listing_type,
        "sort": sort,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
        "categories": categories,
        "items": page_items,
    }


def _get_store_product(*, request: Request, product_id: str) -> dict[str, Any] | None:
    settings = request.app.state.settings
    db = request.app.state.app_db
    if product_id.startswith("seller_"):
        doc = db[settings.marketplace_seller_products_collection].find_one(
            {"product_id": product_id, "status": "active"},
            get_marketplace_projection(),
        )
        return _apply_store_family_range(request, _finalize_store_display(_attach_sales_prediction_fields(request, _attach_app_rating_summaries(request, [serialize_seller_product(doc)])))[0]) if doc else None
    offer_id = product_id.removeprefix("scraped_")
    doc = db[settings.normalized_collection].find_one({"offer_id": offer_id}, _scraped_catalog_projection())
    if not doc and product_id.startswith("scraped_"):
        return None
    if doc:
        return _apply_store_family_range(request, _finalize_store_display(_attach_sales_prediction_fields(request, _attach_app_rating_summaries(request, [serialize_scraped_catalog_product(doc)])))[0])
    return None


def create_app(
    *,
    settings: Settings | None = None,
    pipeline: SearchPipeline | None = None,
    assistant_agent: AssistantAgent | None = None,
    app_db=None,
    mongo_client: MongoClient | None = None,
    rate_limiter=None,
) -> FastAPI:
    app = FastAPI(title="Product Search Agent", version="0.2.3")
    runtime_settings = settings or Settings()
    ensure_indexes(runtime_settings)
    runtime_pipeline = pipeline or SearchPipeline(runtime_settings)
    runtime_mongo = mongo_client or MongoClient(runtime_settings.mongo_uri)
    runtime_db = app_db if app_db is not None else runtime_mongo[runtime_settings.app_db_name]
    runtime_assistant = assistant_agent or AssistantAgent(
        settings=runtime_settings,
        pipeline=runtime_pipeline,
        db=runtime_db,
    )
    logger = setup_logging("api.app")
    runtime_rate_limiter = rate_limiter or _build_rate_limiter(runtime_settings, logger)

    app.state.settings = runtime_settings
    app.state.pipeline = runtime_pipeline
    app.state.mongo_client = runtime_mongo
    app.state.app_db = runtime_db
    app.state.assistant_agent = runtime_assistant
    app.state.rate_limiter = runtime_rate_limiter
    app.state.logger = logger

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(_: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": _validation_error_payload(exc)})

    @app.middleware("http")
    async def rate_limit_and_audit(request: Request, call_next):
        start = time.time()
        runtime = _runtime(request.app)
        settings = runtime["settings"]
        rate_limiter = runtime["rate_limiter"]
        logger = runtime["logger"]
        app_db = runtime["app_db"]

        ip = request.client.host if request.client else "unknown"
        path = request.url.path
        method = request.method
        ua = request.headers.get("user-agent", "")

        if settings.rate_limit_enabled and ip not in {"127.0.0.1", "::1", "localhost"}:
            key = f"{ip}:{path}"
            if not rate_limiter.allow(key):
                duration_ms = int((time.time() - start) * 1000)
                try:
                    write_audit_log(
                        app_db,
                        settings.audit_collection,
                        {
                            "ip": ip,
                            "path": path,
                            "method": method,
                            "status_code": 429,
                            "duration_ms": duration_ms,
                            "user_agent": ua,
                            "rate_limited": True,
                        },
                    )
                except Exception:
                    logger.exception("audit_write_failed")
                REQUEST_COUNT.labels(method=method, path=path, status="429").inc()
                return JSONResponse(status_code=429, content={"detail": "rate_limited"})

        response = await call_next(request)
        duration_ms = int((time.time() - start) * 1000)
        status_code = response.status_code
        REQUEST_COUNT.labels(method=method, path=path, status=str(status_code)).inc()
        REQUEST_LATENCY.labels(method=method, path=path).observe(duration_ms / 1000.0)
        try:
            write_audit_log(
                app_db,
                settings.audit_collection,
                {
                    "ip": ip,
                    "path": path,
                    "method": method,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "user_agent": ua,
                    "rate_limited": False,
                },
            )
        except Exception:
            logger.exception("audit_write_failed")
        if status_code >= 500:
            logger.error("Request failed: %s %s status=%s duration_ms=%s", method, path, status_code, duration_ms)
        return response

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/health/details")
    def health_details(request: Request, _: None = Depends(require_scope("admin"))) -> dict:
        settings = request.app.state.settings
        return {
            "status": "ok",
            "auth_mode": settings.auth_mode,
            "require_auth_for_write": settings.require_auth_for_write,
            "conversation_require_user_id": settings.conversation_require_user_id,
            "rate_limit_enabled": settings.rate_limit_enabled,
            "rate_limit_backend": settings.rate_limit_backend,
            "llm_enabled": settings.llm_enabled,
            "matcher_model": settings.matcher_model_path,
        }

    @app.get("/metrics")
    def metrics(_: None = Depends(require_scope("admin"))) -> Response:
        return PlainTextResponse(generate_latest().decode("utf-8"), media_type=CONTENT_TYPE_LATEST)

    @app.get("/search")
    def search(
        request: Request,
        q: str = Query(..., min_length=2),
        top_k: int = Query(5, ge=1, le=50),
        user_id: str | None = Query(default=None, min_length=1),
    ) -> dict:
        try:
            results = request.app.state.pipeline.search(query=q, top_k=top_k, user_id=user_id)
            return {"query": q, "top_k": top_k, "results": results}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"search_failed: {exc}") from exc

    @app.get("/recommend")
    def recommend(
        request: Request,
        q: str = Query(..., min_length=2, description="Natural language query"),
        top_k: int = Query(5, ge=1, le=50),
        min_rating: float | None = Query(default=None, ge=0, le=5),
        user_id: str | None = Query(default=None, min_length=1),
    ) -> dict:
        try:
            enriched = q if min_rating is None else f"{q} {min_rating}+ stars high rating low price"
            results = request.app.state.pipeline.search(query=enriched, top_k=top_k, user_id=user_id)
            return {
                "query": q,
                "mode": "value_recommendation",
                "top_k": top_k,
                "min_rating": min_rating,
                "user_id": user_id,
                "results": results,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"recommend_failed: {exc}") from exc

    @app.post("/store/auth/register")
    def marketplace_register(request: Request, payload: MarketplaceRegisterRequest) -> dict:
        users = _marketplace_users(request)
        email = payload.email.strip().lower()
        if users.find_one({"email": email}, {"_id": 1}):
            raise HTTPException(status_code=409, detail="marketplace_email_exists")
        user_doc = new_user_doc(
            full_name=payload.full_name,
            email=email,
            password=payload.password,
            role=payload.role,
            store_name=payload.store_name,
            bio=payload.bio,
        )
        users.insert_one(user_doc)
        public_user = serialize_marketplace_user(user_doc)
        token = issue_marketplace_token(public_user, request.app.state.settings)
        return {"token": token, "user": public_user}

    @app.post("/store/auth/login")
    def marketplace_login(request: Request, payload: MarketplaceLoginRequest) -> dict:
        users = _marketplace_users(request)
        user_doc = users.find_one({"email": payload.email.strip().lower()})
        if not user_doc or not verify_password(payload.password, str(user_doc.get("password_hash") or "")):
            raise HTTPException(status_code=401, detail="marketplace_invalid_credentials")
        public_user = serialize_marketplace_user(user_doc)
        token = issue_marketplace_token(public_user, request.app.state.settings)
        return {"token": token, "user": public_user}

    @app.get("/store/auth/me")
    def marketplace_me(user: dict[str, Any] = Depends(require_marketplace_user())) -> dict:
        return {"user": user}

    @app.get("/store/catalog")
    def store_catalog(
        request: Request,
        q: str = Query(default="", max_length=200),
        category: str | None = Query(default=None, max_length=80),
        listing_type: str = Query(default="all", pattern="^(all|scraped|seller)$"),
        seller_id: str | None = Query(default=None, min_length=1, max_length=128),
        min_price: float | None = Query(default=None, ge=0),
        max_price: float | None = Query(default=None, ge=0),
        sort: str = Query(default="newest", pattern="^(newest|relevance|price_asc|price_desc|rating)$"),
        page: int = Query(default=1, ge=1, le=200),
        page_size: int = Query(default=24, ge=1, le=60),
    ) -> dict:
        return _list_store_catalog(
            request=request,
            query=q,
            category=category,
            listing_type=listing_type,
            seller_id=seller_id,
            min_price=min_price,
            max_price=max_price,
            sort=sort,
            page=page,
            page_size=page_size,
        )

    @app.get("/store/products/{product_id}")
    def store_product_detail(request: Request, product_id: str) -> dict:
        product = _get_store_product(request=request, product_id=product_id)
        if not product:
            raise HTTPException(status_code=404, detail="store_product_not_found")
        seller = None
        if product.get("seller_id"):
            seller_doc = _marketplace_users(request).find_one({"user_id": product["seller_id"]}, {"_id": 0, "password_hash": 0})
            if seller_doc:
                seller = serialize_marketplace_user(seller_doc)
        return {"product": product, "seller": seller}

    @app.get("/store/products/{product_id}/reviews")
    def store_product_reviews(
        request: Request,
        product_id: str,
        page: int = Query(default=1, ge=1, le=200),
        page_size: int = Query(default=10, ge=1, le=50),
        authorization: str | None = Header(default=None),
    ) -> dict:
        product = _get_store_product(request=request, product_id=product_id)
        if not product:
            raise HTTPException(status_code=404, detail="store_product_not_found")
        rows = list(
            _marketplace_reviews(request).find(
                {"product_id": product["product_id"]},
                {
                    "_id": 0,
                    "review_id": 1,
                    "product_id": 1,
                    "offer_id": 1,
                    "listing_type": 1,
                    "user_id": 1,
                    "user_name": 1,
                    "user_role": 1,
                    "rating": 1,
                    "title": 1,
                    "body": 1,
                    "created_at": 1,
                    "updated_at": 1,
                },
            ).sort([("updated_at", -1), ("created_at", -1)])
        )
        summary = _summarize_app_reviews(rows)
        current_user = _optional_marketplace_user(request, authorization)
        my_review = None
        if current_user:
            my_review = next((row for row in rows if row.get("user_id") == current_user.get("user_id")), None)
        total = len(rows)
        start = max(0, (page - 1) * page_size)
        end = start + page_size
        return {
            "product_id": product["product_id"],
            "summary": summary,
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [_serialize_marketplace_review(row) for row in rows[start:end]],
            "my_review": _serialize_marketplace_review(my_review) if my_review else None,
        }

    @app.post("/store/products/{product_id}/reviews")
    def submit_store_product_review(
        request: Request,
        product_id: str,
        payload: MarketplaceProductReviewIn,
        user: dict[str, Any] = Depends(require_marketplace_user()),
    ) -> dict:
        product = _get_store_product(request=request, product_id=product_id)
        if not product:
            raise HTTPException(status_code=404, detail="store_product_not_found")
        if product.get("listing_type") == "seller" and product.get("seller_id") == user.get("user_id"):
            raise HTTPException(status_code=403, detail="seller_cannot_review_own_product")
        reviews = _marketplace_reviews(request)
        existing = reviews.find_one(
            {"product_id": product["product_id"], "user_id": user["user_id"]},
            {"_id": 0, "review_id": 1, "created_at": 1},
        )
        doc = _build_review_doc(product=product, user=user, payload=payload, existing=existing)
        reviews.update_one(
            {"product_id": product["product_id"], "user_id": user["user_id"]},
            {"$set": doc},
            upsert=True,
        )
        summary_rows = list(reviews.find({"product_id": product["product_id"]}, {"_id": 0, "rating": 1}))
        return {"review": _serialize_marketplace_review(doc), "summary": _summarize_app_reviews(summary_rows)}

    @app.delete("/store/products/{product_id}/reviews/me")
    def delete_store_product_review(
        request: Request,
        product_id: str,
        user: dict[str, Any] = Depends(require_marketplace_user()),
    ) -> dict:
        product = _get_store_product(request=request, product_id=product_id)
        if not product:
            raise HTTPException(status_code=404, detail="store_product_not_found")
        result = _marketplace_reviews(request).delete_one({"product_id": product["product_id"], "user_id": user["user_id"]})
        summary_rows = list(_marketplace_reviews(request).find({"product_id": product["product_id"]}, {"_id": 0, "rating": 1}))
        return {
            "deleted": result.deleted_count > 0,
            "product_id": product["product_id"],
            "summary": _summarize_app_reviews(summary_rows),
        }

    @app.get("/store/sellers/{seller_id}")
    def store_seller_profile(
        request: Request,
        seller_id: str,
        page: int = Query(default=1, ge=1, le=100),
        page_size: int = Query(default=12, ge=1, le=48),
    ) -> dict:
        seller_doc = _marketplace_users(request).find_one({"user_id": seller_id, "role": "seller"}, {"_id": 0, "password_hash": 0})
        if not seller_doc:
            raise HTTPException(status_code=404, detail="seller_not_found")
        seller = serialize_marketplace_user(seller_doc)
        catalog = _list_store_catalog(
            request=request,
            query="",
            category=None,
            listing_type="seller",
            seller_id=seller_id,
            min_price=None,
            max_price=None,
            sort="newest",
            page=page,
            page_size=page_size,
        )
        return {"seller": seller, "products": catalog}

    @app.get("/store/seller/products")
    def seller_my_products(
        request: Request,
        user: dict[str, Any] = Depends(require_marketplace_user("seller")),
    ) -> dict:
        rows = list(
            _marketplace_products(request).find(
                {"seller_id": user["user_id"], "status": "active"},
                get_marketplace_projection(),
            ).sort([("updated_at", -1)])
        )
        items = [serialize_seller_product(row) for row in rows]
        items = _attach_sales_prediction_fields(request, _attach_app_rating_summaries(request, items))
        return {"items": items}

    @app.post("/store/seller/products")
    def seller_create_product(
        request: Request,
        payload: MarketplaceSellerProductIn,
        user: dict[str, Any] = Depends(require_marketplace_user("seller")),
    ) -> dict:
        doc = build_seller_product_doc(seller=user, payload=payload.model_dump(exclude_none=True))
        _marketplace_products(request).insert_one(doc)
        return {"product": serialize_seller_product(doc)}

    @app.put("/store/seller/products/{product_id}")
    def seller_update_product(
        request: Request,
        product_id: str,
        payload: MarketplaceSellerProductUpdate,
        user: dict[str, Any] = Depends(require_marketplace_user("seller")),
    ) -> dict:
        existing = _marketplace_products(request).find_one(
            {"product_id": product_id, "seller_id": user["user_id"], "status": "active"},
            get_marketplace_projection(),
        )
        if not existing:
            raise HTTPException(status_code=404, detail="seller_product_not_found")
        doc = build_seller_product_doc(
            seller=user,
            payload=payload.model_dump(exclude_none=True),
            existing=existing,
        )
        _marketplace_products(request).update_one({"product_id": product_id}, {"$set": doc})
        return {"product": serialize_seller_product(doc)}

    @app.delete("/store/seller/products/{product_id}")
    def seller_delete_product(
        request: Request,
        product_id: str,
        user: dict[str, Any] = Depends(require_marketplace_user("seller")),
    ) -> dict:
        result = _marketplace_products(request).update_one(
            {"product_id": product_id, "seller_id": user["user_id"], "status": "active"},
            {"$set": {"status": "deleted", "updated_at": datetime.now(timezone.utc)}},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="seller_product_not_found")
        return {"deleted": True, "product_id": product_id}

    @app.post("/store/orders")
    def create_store_order(
        request: Request,
        payload: MarketplaceOrderIn,
        user: dict[str, Any] = Depends(require_marketplace_user("buyer", "seller")),
    ) -> dict:
        product = _get_store_product(request=request, product_id=payload.product_id)
        if not product:
            raise HTTPException(status_code=404, detail="store_product_not_found")
        if not product.get("in_stock", True):
            raise HTTPException(status_code=409, detail="store_product_out_of_stock")
        if product.get("listing_type") == "seller" and product.get("seller_id") == user.get("user_id"):
            raise HTTPException(status_code=403, detail="seller_cannot_order_own_product")
        doc = build_order_doc(
            buyer=user,
            product=product,
            quantity=payload.quantity,
            shipping_address=payload.shipping_address,
            notes=payload.notes,
        )
        _marketplace_orders(request).insert_one(doc)
        if product.get("listing_type") == "seller" and isinstance(product.get("stock_qty"), int):
            next_qty = max(0, int(product["stock_qty"]) - payload.quantity)
            _marketplace_products(request).update_one(
                {"product_id": product["product_id"]},
                {"$set": {"stock_qty": next_qty, "in_stock": next_qty > 0, "updated_at": datetime.now(timezone.utc)}},
            )
        log_interaction(
            settings=request.app.state.settings,
            db=request.app.state.app_db,
            user_id=str(user.get("user_id") or ""),
            offer_id=str(product.get("offer_id") or "").strip() or None,
            source=str(product.get("source") or "") or None,
            link=str(product.get("link") or product.get("external_url") or product.get("internal_path") or "") or None,
            event_type="purchase",
        )
        return {"order": serialize_order(doc)}

    @app.get("/store/orders/me")
    def buyer_orders(
        request: Request,
        user: dict[str, Any] = Depends(require_marketplace_user("buyer", "seller")),
    ) -> dict:
        rows = list(_marketplace_orders(request).find({"buyer_id": user["user_id"]}, {"_id": 0}).sort([("created_at", -1)]))
        return {"items": [serialize_order(row) for row in rows]}

    @app.get("/store/seller/orders")
    def seller_orders(
        request: Request,
        user: dict[str, Any] = Depends(require_marketplace_user("seller")),
    ) -> dict:
        rows = list(_marketplace_orders(request).find({"seller_id": user["user_id"]}, {"_id": 0}).sort([("created_at", -1)]))
        return {"items": [serialize_order(row) for row in rows]}

    @app.put("/store/seller/orders/{order_id}")
    def seller_update_order_status(
        request: Request,
        order_id: str,
        payload: MarketplaceOrderStatusUpdate,
        user: dict[str, Any] = Depends(require_marketplace_user("seller")),
    ) -> dict:
        order = _marketplace_orders(request).find_one({"order_id": order_id, "seller_id": user["user_id"]}, {"_id": 0})
        if not order:
            raise HTTPException(status_code=404, detail="marketplace_order_not_found")
        now = datetime.now(timezone.utc)
        updates: dict[str, Any] = {"status": payload.status, "updated_at": now}
        if payload.status == "paid":
            updates["paid_at"] = now
        if payload.status == "fulfilled":
            updates["fulfilled_at"] = now
        _marketplace_orders(request).update_one({"order_id": order_id}, {"$set": updates})
        order.update(updates)
        return {"order": serialize_order(order)}

    @app.get("/store/seller/reports/summary")
    def seller_reports_summary(
        request: Request,
        user: dict[str, Any] = Depends(require_marketplace_user("seller")),
    ) -> dict:
        items = [
            serialize_seller_product(row)
            for row in _marketplace_products(request).find(
                {"seller_id": user["user_id"], "status": "active"},
                get_marketplace_projection(),
            )
        ]
        product_ids = [str(item.get("product_id") or "") for item in items]
        offer_ids = [str(item.get("offer_id") or "") for item in items]
        orders = _order_rows_for_products(request, product_ids)
        interactions = _interaction_rows_for_offer_ids(request, offer_ids)
        predictions = _prediction_map(request, product_ids)
        ratings_enriched = _attach_app_rating_summaries(request, items)
        analytics = summarize_seller_products(
            items=ratings_enriched,
            orders=orders,
            interactions=interactions,
            predictions_by_product=predictions,
        )
        coverage = {
            "source_rating_count": sum(1 for item in ratings_enriched if item.get("source_rating") is not None or item.get("rating") is not None),
            "source_review_count_count": sum(1 for item in ratings_enriched if int(item.get("source_review_count") or item.get("review_count") or 0) > 0),
        }
        response_payload = {
            "seller_id": user["user_id"],
            "seller_name": user.get("store_name") or user.get("full_name"),
            **analytics,
            "coverage": coverage,
        }
        try:
            report = save_report(
                settings=request.app.state.settings,
                db=request.app.state.app_db,
                owner_user_id=str(user["user_id"]),
                report_type="seller_summary",
                title=f"Seller analytics: {user.get('store_name') or user.get('full_name') or user['user_id']}",
                payload={
                    "filters": {
                        "seller_id": user["user_id"],
                        "seller_name": user.get("store_name") or user.get("full_name"),
                    },
                    "summary": dict(response_payload.get("summary") or {}),
                    "products": list(response_payload.get("products") or []),
                    "notes": [
                        f"Source rating coverage: {coverage['source_rating_count']} products with ratings.",
                        f"Source review-count coverage: {coverage['source_review_count_count']} products with review counts.",
                    ],
                },
                seller_id=str(user["user_id"]),
                source_kind="seller_dashboard",
            )
            response_payload["report_id"] = report["report_id"]
        except Exception as exc:  # pragma: no cover
            logger.warning("seller_summary_report_save_failed seller_id=%s error=%s", user["user_id"], exc)
        return response_payload

    @app.get("/reports")
    def saved_reports_list(
        request: Request,
        user_id: str = Query(..., min_length=1),
        report_type: str | None = Query(default=None, min_length=1),
        _: None = Depends(require_scope("write")),
    ) -> dict:
        items = list_reports(
            settings=request.app.state.settings,
            db=request.app.state.app_db,
            owner_user_id=user_id,
            report_type=report_type,
        )
        return {"items": items, "count": len(items)}

    @app.get("/reports/{report_id}")
    def saved_report_detail(
        request: Request,
        report_id: str,
        user_id: str = Query(..., min_length=1),
        _: None = Depends(require_scope("write")),
    ) -> dict:
        report = get_report(
            settings=request.app.state.settings,
            db=request.app.state.app_db,
            report_id=report_id,
            owner_user_id=user_id,
        )
        if not report:
            raise HTTPException(status_code=404, detail="saved_report_not_found")
        return {"report": report}

    @app.delete("/reports/{report_id}")
    def saved_report_delete(
        request: Request,
        report_id: str,
        user_id: str = Query(..., min_length=1),
        _: None = Depends(require_scope("write")),
    ) -> dict:
        result = request.app.state.app_db[request.app.state.settings.saved_reports_collection].delete_one(
            {"report_id": report_id, "owner_user_id": user_id}
        )
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="saved_report_not_found")
        pdf_path = Path(request.app.state.settings.report_artifacts_dir) / f"{report_id}.pdf"
        try:
            if pdf_path.exists():
                pdf_path.unlink()
        except OSError:
            logger.warning("saved_report_pdf_delete_failed report_id=%s", report_id)
        return {"deleted": True, "report_id": report_id}

    @app.get("/reports/{report_id}/pdf")
    def saved_report_pdf(
        request: Request,
        report_id: str,
        user_id: str = Query(..., min_length=1),
        _: None = Depends(require_scope("write")),
    ) -> FileResponse:
        report = get_report(
            settings=request.app.state.settings,
            db=request.app.state.app_db,
            report_id=report_id,
            owner_user_id=user_id,
        )
        if not report:
            raise HTTPException(status_code=404, detail="saved_report_not_found")
        pdf_path = Path(str(report.get("pdf_path") or "")).resolve()
        if not pdf_path.exists():
            raise HTTPException(status_code=404, detail="saved_report_pdf_missing")
        return FileResponse(
            path=str(pdf_path),
            media_type="application/pdf",
            filename=f"{report_id}.pdf",
        )

    @app.get("/store/reports/source-ratings")
    def source_rating_coverage_report(
        request: Request,
        _: None = Depends(require_scope("admin")),
    ) -> dict:
        rows = list(
            request.app.state.app_db[request.app.state.settings.normalized_collection].find(
                {},
                {"_id": 0, "source": 1, "rating": 1, "review_count": 1},
            )
        )
        by_source: dict[str, dict[str, Any]] = {}
        for row in rows:
            source = str(row.get("source") or "unknown").lower()
            stats = by_source.setdefault(source, {"offers": 0, "with_rating": 0, "with_review_count": 0})
            stats["offers"] += 1
            if row.get("rating") is not None:
                stats["with_rating"] += 1
            if int(row.get("review_count") or 0) > 0:
                stats["with_review_count"] += 1
        for source, stats in by_source.items():
            offers = max(1, int(stats["offers"]))
            stats["rating_coverage_ratio"] = round(stats["with_rating"] / offers, 4)
            stats["review_count_coverage_ratio"] = round(stats["with_review_count"] / offers, 4)
        return {"sources": by_source, "total_offers": len(rows)}

    @app.post("/store/admin/analytics/train")
    def train_marketplace_analytics(
        request: Request,
        payload: MarketplacePredictionTrainRequest,
        _: None = Depends(require_scope("admin")),
    ) -> dict:
        stats = train_marketplace_model(
            request.app.state.settings,
            output_dir=request.app.state.settings.marketplace_dl_model_dir,
            epochs=payload.epochs,
            batch_size=payload.batch_size,
            learning_rate=payload.learning_rate,
            hidden_dim=payload.hidden_dim,
        )
        persist = persist_marketplace_predictions(request.app.state.settings)
        return {"training": stats, "prediction_refresh": persist}

    @app.post("/store/admin/analytics/predict")
    def refresh_marketplace_predictions(
        request: Request,
        _: None = Depends(require_scope("admin")),
    ) -> dict:
        return persist_marketplace_predictions(request.app.state.settings)

    @app.post("/assistant")
    def assistant(request: Request, payload: AssistantRequest, _: None = Depends(require_scope("write"))) -> dict:
        try:
            return request.app.state.assistant_agent.run(
                query=payload.query,
                conversation_id=payload.conversation_id,
                user_id=payload.user_id,
                reference_product_id=payload.reference_product_id,
                top_k=payload.top_k,
                min_rating=payload.min_rating,
                include_tool_trace=payload.include_tool_trace,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=f"assistant_forbidden: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"assistant_failed: {exc}") from exc

    @app.get("/assistant/conversations/{conversation_id}")
    def assistant_history(
        request: Request,
        conversation_id: str,
        limit: int = Query(default=100, ge=1, le=500),
        user_id: str | None = Query(default=None, min_length=1),
        _: None = Depends(require_scope("write")),
    ) -> dict:
        try:
            return request.app.state.assistant_agent.get_history(conversation_id, limit=limit, user_id=user_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=f"assistant_history_forbidden: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"assistant_history_failed: {exc}") from exc

    @app.get("/assistant/conversations/{conversation_id}/search-status")
    def assistant_search_status(
        request: Request,
        conversation_id: str,
        user_id: str | None = Query(default=None, min_length=1),
        _: None = Depends(require_scope("write")),
    ) -> dict:
        try:
            return request.app.state.assistant_agent.get_search_status(conversation_id, user_id=user_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=f"assistant_search_status_forbidden: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"assistant_search_status_failed: {exc}") from exc

    @app.delete("/assistant/conversations/{conversation_id}")
    def assistant_delete_conversation(
        request: Request,
        conversation_id: str,
        user_id: str | None = Query(default=None, min_length=1),
        _: None = Depends(require_scope("write")),
    ) -> dict:
        try:
            return request.app.state.assistant_agent.delete_conversation(conversation_id, user_id=user_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=f"assistant_delete_forbidden: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"assistant_delete_failed: {exc}") from exc

    @app.post("/interactions")
    def interactions(request: Request, payload: InteractionIn, _: None = Depends(require_scope("write"))) -> dict:
        try:
            settings = request.app.state.settings
            doc = log_interaction(
                settings=settings,
                user_id=payload.user_id,
                offer_id=payload.offer_id,
                link=payload.link,
                source=payload.source,
                event_type=payload.event_type,
                event_ts=payload.event_ts,
                event_id=payload.event_id,
                db=request.app.state.app_db,
            )
            return {"status": "logged", "interaction": doc}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"interaction_failed: {exc}") from exc

    @app.get("/labeling/next")
    def labeling_next(
        request: Request,
        limit: int = Query(default=20, ge=1, le=200),
        _: None = Depends(require_scope("admin")),
    ) -> dict:
        try:
            settings = request.app.state.settings
            col = request.app.state.app_db[settings.match_pairs_collection]
            rows = list(
                col.find(
                    {"label": None},
                    {"_id": 0, "pair_id": 1, "title_a": 1, "title_b": 1, "source_a": 1, "source_b": 1, "auto_label": 1},
                ).limit(limit)
            )
            return {"count": len(rows), "pairs": rows}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"labeling_next_failed: {exc}") from exc

    @app.post("/labeling/{pair_id}")
    def labeling_set(
        request: Request,
        pair_id: str,
        label: int = Query(..., ge=0, le=1),
        _: None = Depends(require_scope("admin")),
    ) -> dict:
        try:
            settings = request.app.state.settings
            col = request.app.state.app_db[settings.match_pairs_collection]
            result = col.update_one({"pair_id": pair_id}, {"$set": {"label": int(label)}})
            return {"matched": result.matched_count, "modified": result.modified_count}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"labeling_set_failed: {exc}") from exc

    @app.get("/debug/llm")
    def debug_llm(request: Request, q: str = Query(..., min_length=2), _: None = Depends(require_scope("admin"))) -> dict:
        settings = request.app.state.settings
        parsed = parse_query_with_llm(q, settings)
        return {
            "llm_enabled": settings.llm_enabled,
            "llm_model": settings.llm_model,
            "has_groq_api_key": bool(settings.groq_api_key),
            "parsed": parsed.__dict__,
        }

    return app


app = create_app()
