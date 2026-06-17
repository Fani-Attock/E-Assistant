from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient, TEXT

from src.core.settings import Settings


def get_client(settings: Settings) -> MongoClient:
    return MongoClient(settings.mongo_uri)


def get_db(settings: Settings):
    return get_client(settings)[settings.app_db_name]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_indexes(settings: Settings) -> None:
    db = get_db(settings)

    raw = db[settings.raw_collection]
    raw.create_index([("source", ASCENDING), ("link", ASCENDING)], unique=True, name="uq_source_link")
    raw.create_index([("ingested_at", DESCENDING)], name="idx_ingested_at")
    raw.create_index([("title", TEXT), ("specifications", TEXT)], name="txt_raw_title_specs")

    norm = db[settings.normalized_collection]
    norm.create_index([("source", ASCENDING), ("link", ASCENDING)], unique=True, name="uq_norm_source_link")
    norm.create_index([("title_normalized", TEXT), ("brand", TEXT), ("model", TEXT)], name="txt_norm_search")
    norm.create_index([("brand", ASCENDING)], name="idx_brand")
    norm.create_index([("storage_gb", ASCENDING)], name="idx_storage")
    norm.create_index([("price_pkr", ASCENDING)], name="idx_price")
    norm.create_index([("rating", DESCENDING)], name="idx_rating")
    norm.create_index([("in_stock", ASCENDING)], name="idx_stock")
    norm.create_index([("last_scraped_dt", DESCENDING)], name="idx_last_scraped")

    canonical = db[settings.canonical_collection]
    canonical.create_index([("canonical_id", ASCENDING)], unique=True, name="uq_canonical_id")
    canonical.create_index([("best_offer_price_pkr", ASCENDING)], name="idx_best_price")
    canonical.create_index([("updated_at", DESCENDING)], name="idx_canonical_updated_at")

    history = db[settings.price_history_collection]
    history.create_index([("source", ASCENDING), ("link", ASCENDING), ("observed_at", DESCENDING)], name="idx_history_lookup")

    pairs = db[settings.match_pairs_collection]
    pairs.create_index([("pair_id", ASCENDING)], unique=True, name="uq_pair_id")
    pairs.create_index([("label", ASCENDING)], name="idx_pair_label")

    interactions = db[settings.interactions_collection]
    interactions.create_index([("event_id", ASCENDING)], unique=True, sparse=True, name="uq_event_id")
    interactions.create_index([("user_id", ASCENDING), ("event_ts", DESCENDING)], name="idx_user_events")
    interactions.create_index([("offer_id", ASCENDING), ("event_ts", DESCENDING)], name="idx_offer_events")
    interactions.create_index([("event_type", ASCENDING)], name="idx_event_type")
    interactions.create_index([("is_synthetic", ASCENDING), ("event_ts", DESCENDING)], name="idx_synthetic_event_ts")

    audit = db[settings.audit_collection]
    audit.create_index([("ts", DESCENDING)], name="idx_audit_ts")
    audit.create_index([("path", ASCENDING), ("ts", DESCENDING)], name="idx_audit_path_ts")
    audit.create_index([("ip", ASCENDING), ("ts", DESCENDING)], name="idx_audit_ip_ts")

    tool_logs = db[settings.assistant_tool_logs_collection]
    tool_logs.create_index([("conversation_id", ASCENDING), ("ts", DESCENDING)], name="idx_tool_log_conversation_ts")
    tool_logs.create_index([("tool_name", ASCENDING), ("ts", DESCENDING)], name="idx_tool_log_name_ts")

    sessions = db[settings.conversation_sessions_collection]
    turns = db[settings.conversation_turns_collection]
    ttl_seconds = max(1, settings.conversation_ttl_days) * 24 * 60 * 60
    sessions.create_index([("conversation_id", ASCENDING)], unique=True, name="uq_conversation_id")
    sessions.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)], name="idx_session_user_updated")
    sessions.create_index([("expires_at", ASCENDING)], expireAfterSeconds=ttl_seconds, name="ttl_session_expires")

    turns.create_index([("conversation_id", ASCENDING), ("seq", ASCENDING)], unique=True, name="uq_turn_sequence")
    turns.create_index([("conversation_id", ASCENDING), ("ts", DESCENDING)], name="idx_turn_conversation_ts")
    turns.create_index([("expires_at", ASCENDING)], expireAfterSeconds=ttl_seconds, name="ttl_turn_expires")

    marketplace_users = db[settings.marketplace_users_collection]
    marketplace_users.create_index([("user_id", ASCENDING)], unique=True, name="uq_marketplace_user_id")
    marketplace_users.create_index([("email", ASCENDING)], unique=True, name="uq_marketplace_user_email")
    marketplace_users.create_index([("role", ASCENDING), ("created_at", DESCENDING)], name="idx_marketplace_user_role_created")

    marketplace_products = db[settings.marketplace_seller_products_collection]
    marketplace_products.create_index([("product_id", ASCENDING)], unique=True, name="uq_marketplace_product_id")
    marketplace_products.create_index([("offer_id", ASCENDING)], unique=True, name="uq_marketplace_offer_id")
    marketplace_products.create_index([("seller_id", ASCENDING), ("updated_at", DESCENDING)], name="idx_marketplace_seller_updated")
    marketplace_products.create_index([("status", ASCENDING), ("updated_at", DESCENDING)], name="idx_marketplace_status_updated")
    marketplace_products.create_index([("in_stock", ASCENDING), ("price_pkr", ASCENDING)], name="idx_marketplace_stock_price")
    marketplace_products.create_index(
        [("title_normalized", TEXT), ("brand", TEXT), ("model", TEXT), ("description_normalized", TEXT)],
        name="txt_marketplace_product_search",
    )

    marketplace_reviews = db[settings.marketplace_reviews_collection]
    marketplace_reviews.create_index([("review_id", ASCENDING)], unique=True, name="uq_marketplace_review_id")
    marketplace_reviews.create_index([("product_id", ASCENDING), ("user_id", ASCENDING)], unique=True, name="uq_marketplace_product_user_review")
    marketplace_reviews.create_index([("product_id", ASCENDING), ("updated_at", DESCENDING)], name="idx_marketplace_product_review_updated")
    marketplace_reviews.create_index([("offer_id", ASCENDING), ("updated_at", DESCENDING)], name="idx_marketplace_offer_review_updated")
    marketplace_reviews.create_index([("seller_id", ASCENDING), ("updated_at", DESCENDING)], name="idx_marketplace_seller_review_updated")

    marketplace_orders = db[settings.marketplace_orders_collection]
    marketplace_orders.create_index([("order_id", ASCENDING)], unique=True, name="uq_marketplace_order_id")
    marketplace_orders.create_index([("buyer_id", ASCENDING), ("created_at", DESCENDING)], name="idx_marketplace_order_buyer_created")
    marketplace_orders.create_index([("seller_id", ASCENDING), ("created_at", DESCENDING)], name="idx_marketplace_order_seller_created")
    marketplace_orders.create_index([("product_id", ASCENDING), ("created_at", DESCENDING)], name="idx_marketplace_order_product_created")
    marketplace_orders.create_index([("status", ASCENDING), ("created_at", DESCENDING)], name="idx_marketplace_order_status_created")

    marketplace_predictions = db[settings.marketplace_predictions_collection]
    marketplace_predictions.create_index([("product_id", ASCENDING)], unique=True, name="uq_marketplace_prediction_product")
    marketplace_predictions.create_index([("offer_id", ASCENDING)], name="idx_marketplace_prediction_offer")
    marketplace_predictions.create_index([("updated_at", DESCENDING)], name="idx_marketplace_prediction_updated")

    saved_reports = db[settings.saved_reports_collection]
    saved_reports.create_index([("report_id", ASCENDING)], unique=True, name="uq_saved_report_id")
    saved_reports.create_index([("owner_user_id", ASCENDING), ("created_at", DESCENDING)], name="idx_saved_report_owner_created")
    saved_reports.create_index([("conversation_id", ASCENDING), ("created_at", DESCENDING)], name="idx_saved_report_conversation_created")
    saved_reports.create_index([("report_type", ASCENDING), ("created_at", DESCENDING)], name="idx_saved_report_type_created")
