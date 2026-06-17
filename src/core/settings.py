import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_first(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value != "":
            return value
    return default


@dataclass(frozen=True)
class Settings:
    mongo_uri: str = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    app_db_name: str = os.getenv("APP_DB_NAME", "product_search")
    raw_collection: str = os.getenv("RAW_COLLECTION", "offers_raw")
    normalized_collection: str = os.getenv("NORMALIZED_COLLECTION", "offers_normalized")
    canonical_collection: str = os.getenv("CANONICAL_COLLECTION", "canonical_products")
    price_history_collection: str = os.getenv("PRICE_HISTORY_COLLECTION", "price_history")
    match_pairs_collection: str = os.getenv("MATCH_PAIRS_COLLECTION", "match_pairs_labeled")
    interactions_collection: str = os.getenv("INTERACTIONS_COLLECTION", "user_interactions")
    matcher_model_path: str = os.getenv(
        "MATCHER_MODEL_PATH", "sentence-transformers/all-MiniLM-L6-v2"
    )
    llm_enabled: bool = _env_bool("LLM_ENABLED", default=bool(_env_first("GROQ_API_KEY", "groq_api")))
    llm_model: str = os.getenv("LLM_MODEL", "groq/compound-mini")
    groq_api_key: str | None = _env_first("GROQ_API_KEY", "groq_api")
    service_api_key: str | None = os.getenv("SERVICE_API_KEY")
    admin_api_key: str | None = os.getenv("ADMIN_API_KEY")
    require_auth_for_write: bool = _env_bool(
        "REQUIRE_AUTH_FOR_WRITE",
        default=bool(os.getenv("SERVICE_API_KEY") or os.getenv("ADMIN_API_KEY")),
    )
    max_candidates: int = int(os.getenv("MAX_CANDIDATES", "300"))
    max_cluster_candidates: int = int(os.getenv("MAX_CLUSTER_CANDIDATES", "200"))
    audit_collection: str = os.getenv("AUDIT_COLLECTION", "api_audit_logs")
    rate_limit_enabled: bool = _env_bool("RATE_LIMIT_ENABLED", default=True)
    rate_limit_requests: int = int(os.getenv("RATE_LIMIT_REQUESTS", "120"))
    rate_limit_window_seconds: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
    rate_limit_backend: str = os.getenv("RATE_LIMIT_BACKEND", "memory")  # memory|redis
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    auth_mode: str = os.getenv("AUTH_MODE", "api_key")  # api_key|jwt
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    model_registry_dir: str = os.getenv("MODEL_REGISTRY_DIR", "artifacts/registry")
    scrape_fail_alert_threshold: int = int(os.getenv("SCRAPE_FAIL_ALERT_THRESHOLD", "3"))
    assistant_model: str = os.getenv("ASSISTANT_MODEL", os.getenv("LLM_MODEL", "groq/compound-mini"))
    assistant_max_tool_calls: int = int(os.getenv("ASSISTANT_MAX_TOOL_CALLS", "4"))
    assistant_max_context_turns: int = int(os.getenv("ASSISTANT_MAX_CONTEXT_TURNS", "12"))
    assistant_summary_trigger_turns: int = int(os.getenv("ASSISTANT_SUMMARY_TRIGGER_TURNS", "24"))
    assistant_max_tool_output_chars: int = int(os.getenv("ASSISTANT_MAX_TOOL_OUTPUT_CHARS", "6000"))
    assistant_tool_logs_collection: str = os.getenv("ASSISTANT_TOOL_LOGS_COLLECTION", "assistant_tool_logs")
    conversation_sessions_collection: str = os.getenv("CONVERSATION_SESSIONS_COLLECTION", "chat_sessions")
    conversation_turns_collection: str = os.getenv("CONVERSATION_TURNS_COLLECTION", "chat_turns")
    conversation_ttl_days: int = int(os.getenv("CONVERSATION_TTL_DAYS", "30"))
    live_search_enabled: bool = _env_bool("LIVE_SEARCH_ENABLED", default=True)
    live_search_model: str = os.getenv("LIVE_SEARCH_MODEL", os.getenv("ASSISTANT_MODEL", os.getenv("LLM_MODEL", "groq/compound-mini")))
    live_search_country: str = os.getenv("LIVE_SEARCH_COUNTRY", "pakistan")
    live_search_max_results: int = int(os.getenv("LIVE_SEARCH_MAX_RESULTS", "8"))
    live_search_verify_top_n: int = int(os.getenv("LIVE_SEARCH_VERIFY_TOP_N", "5"))
    inspect_page_enabled: bool = _env_bool("INSPECT_PAGE_ENABLED", default=True)
    inspect_page_timeout_sec: int = int(os.getenv("INSPECT_PAGE_TIMEOUT_SEC", "20"))
    inspect_page_max_redirects: int = int(os.getenv("INSPECT_PAGE_MAX_REDIRECTS", "5"))
    inspect_page_max_response_bytes: int = int(os.getenv("INSPECT_PAGE_MAX_RESPONSE_BYTES", "1200000"))
    inspect_page_block_private_networks: bool = _env_bool("INSPECT_PAGE_BLOCK_PRIVATE_NETWORKS", default=True)
    conversation_require_user_id: bool = _env_bool("CONVERSATION_REQUIRE_USER_ID", default=False)
    marketplace_users_collection: str = os.getenv("MARKETPLACE_USERS_COLLECTION", "marketplace_users")
    marketplace_seller_products_collection: str = os.getenv(
        "MARKETPLACE_SELLER_PRODUCTS_COLLECTION",
        "marketplace_seller_products",
    )
    marketplace_reviews_collection: str = os.getenv(
        "MARKETPLACE_REVIEWS_COLLECTION",
        "marketplace_product_reviews",
    )
    marketplace_orders_collection: str = os.getenv(
        "MARKETPLACE_ORDERS_COLLECTION",
        "marketplace_orders",
    )
    marketplace_predictions_collection: str = os.getenv(
        "MARKETPLACE_PREDICTIONS_COLLECTION",
        "marketplace_product_predictions",
    )
    saved_reports_collection: str = os.getenv(
        "SAVED_REPORTS_COLLECTION",
        "saved_reports",
    )
    marketplace_jwt_secret: str = os.getenv(
        "MARKETPLACE_JWT_SECRET",
        os.getenv("JWT_SECRET") or os.getenv("SERVICE_API_KEY") or "change-me-marketplace-secret",
    )
    marketplace_token_ttl_hours: int = int(os.getenv("MARKETPLACE_TOKEN_TTL_HOURS", "168"))
    marketplace_dl_model_dir: str = os.getenv("MARKETPLACE_DL_MODEL_DIR", "artifacts/marketplace_dl_model")
    report_artifacts_dir: str = os.getenv("REPORT_ARTIFACTS_DIR", "artifacts/reports")
