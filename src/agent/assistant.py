from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
import threading
from typing import Any
from urllib.parse import urlparse

from src.agent.memory import ConversationMemoryStore
from src.core.logging_utils import setup_logging
from src.core.marketplace import lookup_store_product_offer
from src.core.normalize import normalize_text
from src.core.report_store import save_report
from src.core.relevance import filter_relevant_results, query_result_relevance
from src.core.settings import Settings
from src.core.search_pipeline import SearchPipeline
from src.mcp.server import MCPToolError, MCPToolServer

logger = setup_logging("agent.assistant")
_ASYNC_SEARCH_LOCK = threading.Lock()

SYSTEM_PROMPT = """You are a product-search assistant with tool access.
You MUST return only one JSON object in each response.

Allowed output schemas:
1) Tool call:
{"action":"tool","tool_name":"<name>","arguments":{...},"reason":"optional"}

2) Final answer:
{"action":"final","answer":"<plain text answer>","recommended_offer_ids":["optional","offer_ids"]}

Rules:
- Use tools for factual store/product data. Do not invent prices, ratings, links, or availability.
- For new shopping requests, call search_offers first unless enough tool context is already available.
- If the user refers to a prior result by number, ordinal, "this/that/it", or product name, continue with that exact offer.
- For details/specs/price/availability/review follow-ups about a prior result, use get_offer_details and/or inspect_product_page on that offer link instead of starting a new search.
- For compare requests about prior results, compare the referenced products instead of launching a fresh search.
- For refinement requests about prior results (cheaper, under a budget, above a rating, source-only), refine the existing result set before searching again.
- If the user asks a follow-up about prior results but the target product is ambiguous, ask a clarification question instead of guessing or searching.
- Start a new search only when the user asks for a new search, alternatives, or a different product/category.
- If search_offers returns 0 or very few relevant results, call search_web_products for live market offers.
- Use search_web_products for live market coverage, missing local results, or explicitly requested fresh web search; do not use it for simple details about a known returned offer.
- Keep final answer concise and grounded in tool results.
- If tool returns no results, explain that clearly.
"""


def _extract_json_payload(text: str) -> dict[str, Any]:
    content = text.strip()
    if content.startswith("{") and content.endswith("}"):
        return json.loads(content)
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(content[start : end + 1])
    raise ValueError("No JSON object in model output")


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    return value


def _fmt_price(value: Any) -> str:
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return "N/A"


def _display_rating_value(row: dict[str, Any]) -> tuple[float | None, str]:
    value = row.get("display_source_rating")
    kind = str(row.get("display_source_rating_kind") or "").strip().lower() or "missing"
    if value is None:
        value = row.get("source_rating", row.get("rating"))
        if value is not None and kind == "missing":
            kind = "scraped"
    parsed = _safe_float(value)
    return parsed, kind


def _format_price_range(row: dict[str, Any]) -> str | None:
    low = _safe_float(row.get("price_range_pkr_min"))
    high = _safe_float(row.get("price_range_pkr_max"))
    if low is None and high is None:
        return None
    if low is None:
        low = high
    if high is None:
        high = low
    if low is None or high is None:
        return None
    if abs(low - high) < 0.5:
        return f"PKR {_fmt_price(low)}"
    return f"PKR {_fmt_price(low)} - {_fmt_price(high)}"


SOURCE_CONFIDENCE_BASELINE = {
    "daraz": 0.9,
    "shophive": 0.86,
    "ishopping": 0.84,
    "priceoye": 0.85,
    "mega": 0.82,
    "telemart": 0.82,
}

LOW_TRUST_DOMAIN_MARKERS = (
    "blog",
    "wordpress",
    "medium",
    "youtube",
    "facebook",
    "instagram",
    "reddit",
)


def _safe_int(value: Any) -> int | None:
    try:
        parsed = int(float(value))
        if parsed < 0:
            return None
        return parsed
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
        if not math.isfinite(parsed):
            return None
        return parsed
    except (TypeError, ValueError):
        return None


def _should_use_inspected_price(existing_price: Any, inspected_price: Any) -> bool:
    inspected = _safe_float(inspected_price)
    if inspected is None or inspected <= 0:
        return False
    existing = _safe_float(existing_price)
    if existing is None or existing <= 0:
        return True
    # Page text extraction can pick tiny unrelated values from category/list pages.
    # Keep the saved offer price when the inspected value is an obvious outlier.
    if existing >= 1000 and inspected < existing * 0.4:
        return False
    if inspected > existing * 2.5:
        return False
    return True


def _source_confidence_score(source: str, link: str, verification_status: str | None) -> float:
    host = (urlparse(link).netloc or "").lower()
    source_key = (source or "").strip().lower()
    base = 0.58
    for key, weight in SOURCE_CONFIDENCE_BASELINE.items():
        if key in source_key or key in host:
            base = max(base, weight)
    if any(marker in host for marker in LOW_TRUST_DOMAIN_MARKERS):
        base = min(base, 0.35)
    status = (verification_status or "").strip().lower()
    if status == "verified":
        base += 0.16
    elif status == "inspected_no_signals":
        base += 0.06
    elif status == "verify_failed":
        base -= 0.18
    return max(0.0, min(1.0, base))


def _live_offer_value_score(row: dict[str, Any]) -> float:
    price = row.get("total_price_pkr")
    if price is None:
        price = row.get("price_pkr")
    if price is None:
        price_val = None
    else:
        try:
            price_val = float(price)
        except (TypeError, ValueError):
            price_val = None
    rating = row.get("rating")
    if rating is None:
        rating_val = None
    else:
        try:
            rating_val = float(rating)
        except (TypeError, ValueError):
            rating_val = None
    reviews = _safe_int(row.get("review_count"))
    source_conf = float(row.get("source_confidence") or 0.0)

    # Value focus: lower price + stronger rating with confidence penalty.
    price_score = 0.03 if price_val is None else 1.0 / (1.0 + (max(0.0, price_val) / 220000.0))
    rating_score = 0.0 if rating_val is None else max(0.0, min(1.0, rating_val / 5.0))
    review_score = 0.0 if reviews is None else min(1.0, math.log1p(reviews) / 6.0)

    return float(0.42 * price_score + 0.33 * rating_score + 0.10 * review_score + 0.15 * source_conf)


def _normalize_live_offers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title", "")).strip()
        link = str(row.get("link", "")).strip()
        if not title or not link:
            continue
        price = row.get("price_pkr")
        try:
            total_price = float(price) if price is not None else None
        except (TypeError, ValueError):
            total_price = None
        rating = row.get("rating")
        try:
            rating_val = float(rating) if rating is not None else None
        except (TypeError, ValueError):
            rating_val = None
        verification_status = str(row.get("verification_status", "unverified")).strip().lower() or "unverified"
        verified_fields = row.get("verified_fields")
        if not isinstance(verified_fields, list):
            verified_fields = []
        review_count = _safe_int(row.get("review_count"))
        source_conf = _source_confidence_score(
            source=str(row.get("source", "web")),
            link=link,
            verification_status=verification_status,
        )
        out.append(
            {
                "title": title,
                "link": link,
                "source": str(row.get("source", "web")).strip().lower() or "web",
                "image": row.get("image"),
                "price_pkr": total_price,
                "total_price_pkr": total_price,
                "rating": rating_val,
                "review_count": review_count,
                "match_score": None,
                "rank_score": None,
                "reason": str(row.get("reason", "")).strip() or "Live web search result.",
                "verification_status": verification_status,
                "verified_fields": list(verified_fields),
                "verified_at": row.get("verified_at"),
                "source_confidence": source_conf,
            }
        )
    out.sort(key=lambda r: (_live_offer_value_score(r), -_price_for_sort(r)), reverse=True)
    return out


def _query_relevance_score(query: str, rows: list[dict[str, Any]]) -> float:
    return float(query_result_relevance(query, rows))


def _price_for_sort(row: dict[str, Any]) -> float:
    value = row.get("total_price_pkr")
    if value is None:
        value = row.get("price_pkr")
    if value is None:
        return float("inf")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


def _is_review_query(query: str) -> bool:
    q = normalize_text(query)
    markers = ("review", "reviews", "rating", "ratings", "stars", "feedback", "opinion", "opinions")
    return any(x in q for x in markers)


def _has_explicit_reference_cue(query: str) -> bool:
    q = normalize_text(query)
    tokens = set(q.split())
    if {"this", "that", "it"}.intersection(tokens):
        return True
    markers = (
        "same product",
        "same item",
        "same watch",
        "same phone",
        "that one",
        "this one",
        "product no",
        "product number",
        "item no",
        "item number",
        "option no",
        "option number",
        "offer no",
        "offer number",
        "listing no",
        "listing number",
    )
    if any(marker in q for marker in markers):
        return True
    if re.search(r"\b(?:product|item|option|offer|listing|result)\s*(?:no|number)?\s*\d{1,2}\b", q):
        return True
    if re.search(r"\b\d{1,2}(?:st|nd|rd|th)?\s*(?:product|item|option|offer|listing|result|one)\b", q):
        return True
    ordinal_markers = (
        "first",
        "1st",
        "second",
        "2nd",
        "third",
        "3rd",
        "fourth",
        "4th",
        "fifth",
        "5th",
    )
    return any(marker in tokens for marker in ordinal_markers)


def _is_reference_query(query: str) -> bool:
    return _has_explicit_reference_cue(query)


def _is_new_search_query(query: str) -> bool:
    q = normalize_text(query)
    if not q:
        return False
    if _has_explicit_reference_cue(q):
        return False
    explicit_markers = (
        "new search",
        "start new search",
        "start over",
        "search again",
        "forget this",
        "different product",
        "different category",
        "another product",
        "other products",
    )
    if any(marker in q for marker in explicit_markers):
        return True
    strong_search_prefixes = (
        "search for ",
        "search ",
        "find ",
        "find me ",
        "look for ",
        "recommend ",
        "recommend me ",
    )
    if any(q.startswith(prefix) for prefix in strong_search_prefixes):
        return True
    show_prefixes = ("show me ",)
    if any(q.startswith(prefix) for prefix in show_prefixes):
        followup_markers = ("detail", "details", "more", "review", "rating", "spec", "price", "availability")
        return not any(marker in q for marker in followup_markers)
    return False


def _is_product_followup_query(query: str) -> bool:
    if _is_new_search_query(query):
        return False
    q = normalize_text(query)
    markers = (
        "detail",
        "details",
        "delivery",
        "shipping",
        "courier",
        "charges",
        "charge",
        "fee",
        "fees",
        "more about",
        "tell me about",
        "explore",
        "inspect",
        "open",
        "check",
        "spec",
        "specs",
        "specification",
        "specifications",
        "feature",
        "features",
        "description",
        "warranty",
        "availability",
        "available",
        "stock",
        "seller",
        "store",
        "link",
        "page",
        "price",
        "review",
        "reviews",
        "rating",
        "ratings",
        "worth",
        "good",
        "buy",
    )
    return _has_explicit_reference_cue(q) or any(marker in q for marker in markers)


REFERENCE_STOPWORDS = {
    "about",
    "available",
    "availability",
    "buy",
    "check",
    "description",
    "detail",
    "details",
    "explore",
    "feature",
    "features",
    "for",
    "give",
    "good",
    "inspect",
    "is",
    "link",
    "me",
    "more",
    "open",
    "page",
    "price",
    "rating",
    "ratings",
    "review",
    "reviews",
    "seller",
    "show",
    "spec",
    "specification",
    "specifications",
    "specs",
    "stock",
    "store",
    "tell",
    "the",
    "warranty",
    "worth",
}

GENERIC_PRODUCT_TOKENS = {
    "best",
    "buds",
    "earbud",
    "earbuds",
    "gaming",
    "headphone",
    "headphones",
    "mobile",
    "phone",
    "product",
    "smart",
    "watch",
    "wireless",
}


def _token_variants(tokens: set[str]) -> set[str]:
    variants: set[str] = set()
    for token in tokens:
        value = token.strip().lower()
        if not value:
            continue
        variants.add(value)
        compact = re.sub(r"[^a-z0-9]", "", value)
        if compact:
            variants.add(compact)
    return variants


def _is_distinctive_reference_token(token: str) -> bool:
    value = token.strip().lower()
    if not value or value in REFERENCE_STOPWORDS:
        return False
    if any(ch.isdigit() for ch in value):
        return len(value) >= 2
    if value in GENERIC_PRODUCT_TOKENS:
        return False
    return len(value) >= 4


def _query_has_any(query: str, markers: tuple[str, ...]) -> bool:
    q = normalize_text(query)
    return any(marker in q for marker in markers)


def _is_delivery_query(query: str) -> bool:
    return _query_has_any(
        query,
        (
            "delivery",
            "shipping",
            "courier",
            "shipment",
            "shipping charge",
            "shipping charges",
            "delivery charge",
            "delivery charges",
            "delivery fee",
            "shipping fee",
            "cash on delivery",
            "cod",
        ),
    )


def _is_warranty_query(query: str) -> bool:
    return _query_has_any(query, ("warranty", "guarantee", "guaranty"))


def _is_availability_query(query: str) -> bool:
    return _query_has_any(query, ("availability", "available", "in stock", "out of stock", "stock"))


def _is_price_query(query: str) -> bool:
    return _query_has_any(query, ("price", "cost", "how much", "total"))


def _is_specs_query(query: str) -> bool:
    return _query_has_any(
        query,
        ("spec", "specs", "specification", "specifications", "feature", "features", "capacity", "details"),
    )


def _is_sales_query(query: str) -> bool:
    return _query_has_any(query, ("sold", "sales", "revenue", "orders", "order count", "units sold"))


def _is_seasonality_query(query: str) -> bool:
    return _query_has_any(query, ("season", "month", "best month", "best season", "summer", "winter", "june", "july"))


def _response_focus(query: str) -> str:
    if _is_review_query(query):
        return "reviews"
    if _is_delivery_query(query):
        return "delivery"
    if _is_warranty_query(query):
        return "warranty"
    if _is_availability_query(query):
        return "availability"
    if _is_price_query(query):
        return "price"
    if _is_specs_query(query):
        return "specs"
    if _is_sales_query(query):
        return "sales"
    if _is_seasonality_query(query):
        return "seasonality"
    return "general"


def _is_compare_query(query: str) -> bool:
    if _is_new_search_query(query):
        return False
    q = normalize_text(query)
    markers = (
        "compare",
        "comparison",
        "how do they compare",
        "how do these compare",
        "how do they differ",
        "how are they different",
        "vs",
        "vs.",
        "versus",
        "better than",
        "better value",
        "better buy",
        "difference between",
        "differences between",
        "which is better",
        "which one is better",
        "better option",
        "worth the extra",
        "worth paying more",
        "worth the higher price",
        "worth the price difference",
        "is it worth paying more",
    )
    return any(marker in q for marker in markers)


def _is_general_question(query: str) -> bool:
    if _is_new_search_query(query) or _is_product_followup_query(query) or _is_compare_query(query):
        return False
    q = normalize_text(query)
    prefixes = (
        "what is ",
        "what are ",
        "what was ",
        "what were ",
        "how does ",
        "how do ",
        "why is ",
        "why are ",
        "explain ",
        "can you explain ",
    )
    if any(q.startswith(prefix) for prefix in prefixes):
        return True
    markers = (
        "what are we comparing",
        "which product are we discussing",
        "what product are we discussing",
        "what was the last search",
        "what were the last results",
        "what is the selected product",
        "what is the current product",
        "why are you comparing",
        "why did you compare",
        "why did you search",
        "why are you searching",
        "why did you inspect",
        "why did you check the page",
        "why did you choose this action",
    )
    return any(marker in q for marker in markers)


def _parse_numeric_amount(raw: str, suffix: str | None = None) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    scale = (suffix or "").strip().lower()
    if scale == "k":
        value *= 1000.0
    elif scale in {"lac", "lakh"}:
        value *= 100000.0
    elif scale == "m":
        value *= 1000000.0
    return value


def _extract_max_price(query: str) -> float | None:
    q = normalize_text(query)
    patterns = (
        r"(?:under|below|less than|upto|up to|max|maximum)\s*(?:pkr|rs\.?|rs)?\s*(\d+(?:\.\d+)?)\s*(k|m|lac|lakh)?",
        r"(?:pkr|rs\.?|rs)\s*(\d+(?:\.\d+)?)\s*(k|m|lac|lakh)?\s*(?:or less|or below|max(?:imum)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, q)
        if not match:
            continue
        parsed = _parse_numeric_amount(match.group(1), match.group(2))
        if parsed is not None:
            return parsed
    return None


def _extract_min_price(query: str) -> float | None:
    q = normalize_text(query)
    patterns = (
        r"(?:above|over|more than|at least|min|minimum)\s*(?:pkr|rs\.?|rs)?\s*(\d+(?:\.\d+)?)\s*(k|m|lac|lakh)?",
    )
    for pattern in patterns:
        match = re.search(pattern, q)
        if not match:
            continue
        parsed = _parse_numeric_amount(match.group(1), match.group(2))
        if parsed is not None:
            return parsed
    return None


def _extract_min_rating(query: str) -> float | None:
    q = normalize_text(query)
    patterns = (
        r"(?:rating|ratings|review score|stars?)\s*(?:above|over|at least|min|minimum)\s*(\d(?:\.\d)?)",
        r"(?:above|over|at least|min|minimum)\s*(\d(?:\.\d)?)\s*(?:rating|ratings|stars?)",
    )
    for pattern in patterns:
        match = re.search(pattern, q)
        if not match:
            continue
        try:
            value = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if 0.0 <= value <= 5.0:
            return value
    return None


def _extract_source_filters(query: str) -> list[str]:
    q = normalize_text(query)
    known_sources = ("daraz", "priceoye", "ishopping", "shophive", "telemart", "mega", "ronin", "audionic")
    return [source for source in known_sources if source in q]


REFINE_HINT_MARKERS = (
    "cheaper",
    "cheapest",
    "budget",
    "budget friendly",
    "budget-friendly",
    "more affordable",
    "affordable",
    "under ",
    "below ",
    "less than",
    "above ",
    "over ",
    "at least",
    "minimum",
    "sort by price",
    "lowest price",
    "highest rating",
    "best rated",
    "top rated",
    "most reviewed",
    "best reviews",
    "highest reviews",
    "better reviewed",
    "better rating",
    "only ",
    "from ",
    "show cheaper",
    "show only",
    "same brand",
    "same source",
    "same store",
    "filter",
)


REFINE_STOPWORDS = REFERENCE_STOPWORDS.union(
    {
        "and",
        "any",
        "below",
        "between",
        "best",
        "brand",
        "cheaper",
        "cheapest",
        "from",
        "high",
        "higher",
        "keep",
        "low",
        "lower",
        "match",
        "matches",
        "only",
        "option",
        "options",
        "previous",
        "rating",
        "results",
        "search",
        "show",
        "similar",
        "sort",
        "than",
        "under",
        "with",
    }
)


def _extract_refine_tokens(query: str) -> set[str]:
    tokens = set(normalize_text(query).split())
    return {token for token in tokens if token and token not in REFINE_STOPWORDS and len(token) >= 3}


def _is_refine_query(query: str) -> bool:
    if _is_new_search_query(query) or _is_compare_query(query):
        return False
    q = normalize_text(query)
    if any(marker in q for marker in REFINE_HINT_MARKERS):
        return True
    return _extract_max_price(query) is not None or _extract_min_price(query) is not None or _extract_min_rating(query) is not None


def _punctuate_line(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return value
    if value[-1] in ".!?":
        return value
    return value + "."



class AssistantAgent:
    def __init__(self, settings: Settings, pipeline: SearchPipeline | None = None, db=None):
        self.settings = settings
        self.pipeline = pipeline or SearchPipeline(settings)
        self.db = db if db is not None else self.pipeline.client[settings.app_db_name]
        self.tools = MCPToolServer(settings=settings, pipeline=self.pipeline, db=self.db)
        self.memory = ConversationMemoryStore(settings=settings, db=self.db)

    @staticmethod
    def _state_results(state: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(state, dict):
            return []
        rows = state.get("last_results")
        if isinstance(rows, list) and rows:
            return rows
        rows = state.get("last_search_results")
        if isinstance(rows, list):
            return rows
        return []

    def _compact_offer(self, offer: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(offer, dict):
            return None
        compact = self._compact_results([offer])
        if compact:
            return compact[0]
        link = str(offer.get("link", "")).strip()
        title = str(offer.get("title", "")).strip()
        if not link or not title:
            return None
        return {
            "offer_id": offer.get("offer_id"),
            "title": title,
            "link": link,
            "source": str(offer.get("source", "web")).strip().lower() or "web",
        }

    def _extract_reference_indexes(self, *, query: str, rows_count: int, allow_bare_numbers: bool = False) -> list[int]:
        if rows_count <= 0:
            return []
        normalized_query = normalize_text(query)
        indexes: list[int] = []
        ordinal_map = {
            "first": 0,
            "1st": 0,
            "second": 1,
            "2nd": 1,
            "third": 2,
            "3rd": 2,
            "fourth": 3,
            "4th": 3,
            "fifth": 4,
            "5th": 4,
        }
        for token, idx in ordinal_map.items():
            if token in normalized_query and idx < rows_count and idx not in indexes:
                indexes.append(idx)

        patterns = [
            r"(?:product|item|option|offer|listing|result)\s*(?:no|number|#)?\s*(\d{1,2})\b",
            r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:product|item|option|offer|listing|result|one)\b",
        ]
        if allow_bare_numbers:
            patterns.append(r"\b(\d{1,2})\b")
        for pattern in patterns:
            for match in re.finditer(pattern, normalized_query):
                idx = int(match.group(1)) - 1
                if 0 <= idx < rows_count and idx not in indexes:
                    indexes.append(idx)
        return indexes

    def _resolve_comparison_offers(self, *, query: str, state: dict[str, Any]) -> list[dict[str, Any]]:
        rows = self._state_results(state)
        if not rows:
            return []
        selected: list[dict[str, Any]] = []
        seen_links: set[str] = set()

        def add_offer(candidate: dict[str, Any] | None) -> None:
            if not isinstance(candidate, dict):
                return
            link = str(candidate.get("link", "")).strip()
            if not link or link in seen_links:
                return
            seen_links.add(link)
            selected.append(candidate)

        allow_bare_numbers = _is_compare_query(query)
        for idx in self._extract_reference_indexes(query=query, rows_count=len(rows), allow_bare_numbers=allow_bare_numbers):
            add_offer(rows[idx] if isinstance(rows[idx], dict) else None)

        active_offer = state.get("active_offer") or state.get("last_reference_offer")
        if _has_explicit_reference_cue(query) and any(token in normalize_text(query).split() for token in ("this", "that", "it")):
            add_offer(active_offer if isinstance(active_offer, dict) else None)

        if len(selected) == 1 and isinstance(active_offer, dict):
            add_offer(active_offer)

        if len(selected) >= 2:
            return selected[:2]

        meaningful_variants = _token_variants(_extract_refine_tokens(query))
        if meaningful_variants:
            scored: list[tuple[int, dict[str, Any]]] = []
            for row in rows[:10]:
                if not isinstance(row, dict):
                    continue
                title_variants = _token_variants(set(normalize_text(str(row.get("title", ""))).split()))
                overlap = len(meaningful_variants.intersection(title_variants))
                if overlap > 0:
                    scored.append((overlap, row))
            scored.sort(key=lambda item: item[0], reverse=True)
            for _, row in scored:
                add_offer(row)
                if len(selected) >= 2:
                    break
        return selected[:2]

    def _build_turn_plan(
        self,
        *,
        query: str,
        state: dict[str, Any],
        top_k: int,
        min_rating: float | None,
    ) -> dict[str, Any]:
        rows = self._state_results(state)
        reference_offer = self._resolve_reference_offer(query=query, state=state)
        comparison_offers = self._resolve_comparison_offers(query=query, state=state) if _is_compare_query(query) else []
        target_indexes = self._extract_reference_indexes(
            query=query,
            rows_count=len(rows),
            allow_bare_numbers=_is_compare_query(query),
        )
        focus = _response_focus(query)

        plan: dict[str, Any] = {
            "intent": "new_search",
            "target_offer": self._compact_offer(reference_offer),
            "target_result_indexes": target_indexes,
            "comparison_offers": [self._compact_offer(row) for row in comparison_offers if self._compact_offer(row)],
            "requires_local_search": False,
            "requires_page_inspection": False,
            "requires_live_web_search": False,
            "response_focus": focus,
            "clarification_question": None,
            "top_k": top_k,
            "min_rating": min_rating,
        }

        if _is_new_search_query(query):
            plan["intent"] = "new_search"
            plan["requires_local_search"] = True
            return plan

        if _is_compare_query(query):
            if len(comparison_offers) >= 2:
                plan["intent"] = "compare_products"
                plan["requires_page_inspection"] = True
                return plan
            if len(rows) >= 2:
                plan["intent"] = "clarification_needed"
                plan["clarification_question"] = (
                    "Which two products do you want me to compare? Say 'compare product 1 and 3' or name them."
                )
                return plan

        if _is_refine_query(query) and rows:
            plan["intent"] = "refine_previous_results"
            return plan

        if reference_offer is not None and _is_review_query(query):
            plan["intent"] = "selected_product_reviews"
            plan["requires_page_inspection"] = True
            return plan

        if reference_offer is not None and _is_specs_query(query):
            plan["intent"] = "selected_product_specs"
            plan["requires_page_inspection"] = True
            return plan

        if reference_offer is not None and (
            _is_delivery_query(query) or _is_warranty_query(query) or _is_availability_query(query)
        ):
            plan["intent"] = "selected_product_logistics"
            plan["requires_page_inspection"] = True
            return plan

        if reference_offer is not None and (_is_price_query(query) or _is_product_followup_query(query)):
            plan["intent"] = "selected_product_followup"
            plan["requires_page_inspection"] = True
            return plan

        if len(rows) > 1 and (
            _is_product_followup_query(query)
            or _is_review_query(query)
            or _is_reference_query(query)
        ):
            plan["intent"] = "clarification_needed"
            plan["clarification_question"] = (
                "Which product do you want me to continue with? Say 'product 2' or the product name."
            )
            return plan

        if _is_general_question(query):
            plan["intent"] = "general_question"
            return plan

        plan["intent"] = "new_search"
        plan["requires_local_search"] = True
        return plan

    def _validate_turn_plan(self, *, query: str, state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        valid_intents = {
            "new_search",
            "selected_product_followup",
            "selected_product_logistics",
            "selected_product_reviews",
            "selected_product_specs",
            "compare_products",
            "refine_previous_results",
            "general_question",
            "clarification_needed",
        }
        normalized = dict(plan or {})
        intent = str(normalized.get("intent") or "")
        if intent not in valid_intents:
            raise ValueError(f"invalid_plan_intent:{intent}")

        target_offer = normalized.get("target_offer")
        comparison_offers = normalized.get("comparison_offers")
        if comparison_offers is None:
            comparison_offers = []
            normalized["comparison_offers"] = comparison_offers
        if not isinstance(comparison_offers, list):
            raise ValueError("invalid_plan_comparison_offers")
        if normalized.get("target_result_indexes") is None:
            normalized["target_result_indexes"] = []
        if not isinstance(normalized.get("target_result_indexes"), list):
            raise ValueError("invalid_plan_target_result_indexes")

        if intent.startswith("selected_product_"):
            if not isinstance(target_offer, dict) or not str(target_offer.get("link", "")).strip():
                raise ValueError("selected_product_plan_missing_target_offer")
            normalized["requires_page_inspection"] = True
            normalized["requires_local_search"] = False
            normalized["requires_live_web_search"] = False
        elif intent == "compare_products":
            compact_comparison = [self._compact_offer(row) for row in comparison_offers if self._compact_offer(row)]
            if len(compact_comparison) < 2:
                normalized["intent"] = "clarification_needed"
                normalized["comparison_offers"] = compact_comparison
                normalized["clarification_question"] = (
                    "Which two products do you want me to compare? Say 'compare product 1 and 3' or name them."
                )
            else:
                normalized["comparison_offers"] = compact_comparison[:2]
                normalized["requires_page_inspection"] = True
                normalized["requires_local_search"] = False
                normalized["requires_live_web_search"] = False
        elif intent == "refine_previous_results":
            if not self._state_results(state):
                normalized["intent"] = "new_search"
                normalized["requires_local_search"] = True
        elif intent == "general_question":
            normalized["requires_local_search"] = False
            normalized["requires_page_inspection"] = False
            normalized["requires_live_web_search"] = False
        elif intent == "clarification_needed":
            question = str(normalized.get("clarification_question") or "").strip()
            if not question:
                raise ValueError("clarification_plan_missing_question")
            normalized["requires_local_search"] = False
            normalized["requires_page_inspection"] = False
            normalized["requires_live_web_search"] = False
        elif intent == "new_search":
            normalized["requires_local_search"] = True

        if intent == "new_search" and _is_general_question(query):
            normalized["intent"] = "general_question"
            normalized["requires_local_search"] = False
        return normalized

    @staticmethod
    def _is_tool_allowed_for_plan(plan: dict[str, Any] | None, tool_name: str, *, has_search_context: bool) -> bool:
        intent = str((plan or {}).get("intent") or "")
        if not intent:
            return True
        if intent == "new_search":
            if tool_name in {"search_offers", "search_web_products", "log_interaction", "report_interactions"}:
                return True
            if tool_name in {"get_offer_details", "inspect_product_page"}:
                return has_search_context
            return False
        if intent in {"selected_product_followup", "selected_product_logistics", "selected_product_reviews", "selected_product_specs"}:
            return tool_name in {"get_offer_details", "inspect_product_page", "log_interaction"}
        if intent == "compare_products":
            return tool_name in {"get_offer_details", "inspect_product_page"}
        if intent == "refine_previous_results":
            return tool_name in {"log_interaction"}
        if intent in {"clarification_needed", "general_question"}:
            return tool_name in {"log_interaction"}
        return True

    def _build_state_patch(
        self,
        *,
        query: str,
        state: dict[str, Any],
        plan: dict[str, Any],
        response: dict[str, Any],
    ) -> dict[str, Any]:
        prior_results = self._state_results(state)
        compact_results = self._compact_results(list(response.get("results") or []))
        active_offer = self._compact_offer(state.get("active_offer")) or self._compact_offer(state.get("last_reference_offer"))
        intent = str(plan.get("intent") or "")
        patch: dict[str, Any] = {
            "last_intent": intent,
            "last_plan": {
                "intent": intent,
                "response_focus": plan.get("response_focus"),
                "target_result_indexes": list(plan.get("target_result_indexes") or []),
                "requires_local_search": bool(plan.get("requires_local_search")),
                "requires_page_inspection": bool(plan.get("requires_page_inspection")),
                "requires_live_web_search": bool(plan.get("requires_live_web_search")),
                "target_offer": self._compact_offer(plan.get("target_offer")),
                "comparison_offers": [
                    self._compact_offer(row)
                    for row in list(plan.get("comparison_offers") or [])
                    if self._compact_offer(row)
                ],
            },
            "last_tool_outputs": self._compact_tool_outputs(list(response.get("tool_calls") or [])),
        }

        if intent == "new_search":
            patch["last_results"] = compact_results
            patch["last_search_results"] = compact_results
            patch["last_results_query"] = query
            single = compact_results[0] if len(compact_results) == 1 else None
            patch["active_offer"] = single
            patch["last_reference_offer"] = single
            patch["active_offer_details"] = single
            patch["active_comparison_set"] = []
            return patch

        if intent in {"selected_product_followup", "selected_product_logistics", "selected_product_specs", "selected_product_reviews"}:
            selected = compact_results[0] if compact_results else self._compact_offer(plan.get("target_offer")) or active_offer
            patch["last_results"] = prior_results
            patch["active_offer"] = selected
            patch["last_reference_offer"] = selected
            patch["active_offer_details"] = selected
            patch["active_comparison_set"] = []
            return patch

        if intent == "compare_products":
            comparison = compact_results[:2] or [
                self._compact_offer(row) for row in list(plan.get("comparison_offers") or []) if self._compact_offer(row)
            ]
            patch["last_results"] = prior_results
            patch["active_comparison_set"] = comparison
            if comparison:
                patch["last_reference_offer"] = comparison[0]
            return patch

        if intent == "refine_previous_results":
            patch["last_results"] = compact_results
            if isinstance(state.get("last_search_results"), list):
                patch["last_search_results"] = state.get("last_search_results")
            else:
                patch["last_search_results"] = prior_results
            patch["last_results_query"] = query
            if len(compact_results) == 1:
                patch["active_offer"] = compact_results[0]
                patch["last_reference_offer"] = compact_results[0]
            patch["active_comparison_set"] = []
            return patch

        if intent == "clarification_needed":
            patch["last_results"] = prior_results
            return patch

        if intent == "general_question":
            patch["last_results"] = prior_results
            return patch

        patch["last_results"] = compact_results or prior_results
        return patch

    @staticmethod
    def _compact_tool_outputs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for row in rows[:10]:
            if not isinstance(row, dict):
                continue
            output = row.get("output")
            ok = None
            error = None
            if isinstance(output, dict):
                ok = output.get("ok")
                error = output.get("error")
            compact.append(
                {
                    "tool_name": row.get("tool_name"),
                    "ok": ok,
                    "error": error,
                }
            )
        return compact

    def _build_assistant_context(
        self,
        *,
        query: str,
        state: dict[str, Any],
        plan: dict[str, Any],
        response: dict[str, Any],
    ) -> dict[str, Any]:
        intent = str(plan.get("intent") or "")
        selected = None
        results = list(response.get("results") or [])
        if results:
            selected = self._compact_offer(results[0])
        if selected is None:
            selected = self._compact_offer(plan.get("target_offer")) or self._compact_offer(state.get("active_offer"))

        comparison_offers = []
        for row in list(plan.get("comparison_offers") or []):
            compact = self._compact_offer(row)
            if compact:
                comparison_offers.append(compact)
        if not comparison_offers and intent == "compare_products":
            comparison_offers = [self._compact_offer(row) for row in results[:2] if self._compact_offer(row)]

        decision_reason = self._build_plan_reason(query=query, state=state, plan=plan)
        if intent == "selected_product_logistics":
            summary = f"Continuing with selected product{': ' + selected['title'] if selected else ''}; checking delivery, availability, or warranty."
            mode_label = "Selected Product"
        elif intent == "selected_product_reviews":
            summary = f"Continuing with selected product{': ' + selected['title'] if selected else ''}; checking reviews and ratings."
            mode_label = "Selected Product"
        elif intent == "selected_product_specs":
            summary = f"Continuing with selected product{': ' + selected['title'] if selected else ''}; checking specs and features."
            mode_label = "Selected Product"
        elif intent == "selected_product_followup":
            summary = f"Continuing with selected product{': ' + selected['title'] if selected else ''}."
            mode_label = "Selected Product"
        elif intent == "compare_products":
            names = [str(row.get("title", "")).strip() for row in comparison_offers if isinstance(row, dict)]
            summary = "Comparing prior results" + (f": {' vs '.join(names[:2])}" if names else ".")
            mode_label = "Comparison"
        elif intent == "refine_previous_results":
            summary = "Refining the previous result set with your new constraints."
            mode_label = "Refinement"
        elif intent == "clarification_needed":
            summary = "Need clarification before continuing."
            mode_label = "Clarification"
        elif intent == "general_question":
            summary = "Answering from conversation context."
            mode_label = "Conversation"
        else:
            summary = "Starting a fresh search for matching products."
            mode_label = "Search"

        return {
            "intent": intent,
            "mode_label": mode_label,
            "response_focus": plan.get("response_focus"),
            "summary": summary,
            "decision_reason": decision_reason,
            "selected_offer": selected,
            "comparison_offers": comparison_offers,
            "results_query": state.get("last_results_query") or query,
        }

    def _build_plan_reason(self, *, query: str, state: dict[str, Any], plan: dict[str, Any]) -> str:
        intent = str(plan.get("intent") or "")
        focus = str(plan.get("response_focus") or "general")
        target = self._compact_offer(plan.get("target_offer")) or self._compact_offer(state.get("active_offer"))
        comparison: list[dict[str, Any]] = []
        for row in list(plan.get("comparison_offers") or []):
            compact = self._compact_offer(row)
            if compact is not None:
                comparison.append(compact)
        last_query = str(state.get("last_results_query") or "").strip()

        if intent in {"selected_product_followup", "selected_product_logistics", "selected_product_reviews", "selected_product_specs"}:
            title = str((target or {}).get("title") or "the selected product").strip()
            if focus == "delivery":
                return f"I stayed on {title} because your question is about delivery, availability, or warranty for the current product."
            if focus == "reviews":
                return f"I stayed on {title} because your question asks for review or rating details on the current product."
            if focus == "specs":
                return f"I stayed on {title} because your question asks for product specs or features rather than a new search."
            return f"I stayed on {title} because this message reads like a follow-up on the current product, not a new search."
        if intent == "compare_products":
            if len(comparison) >= 2:
                left = str(comparison[0].get("title") or "product 1").strip()
                right = str(comparison[1].get("title") or "product 2").strip()
                return f"I compared {left} and {right} because you asked for a comparison across the existing result set."
            return "I stayed inside the current result set because your message asks for a comparison, not a fresh search."
        if intent == "refine_previous_results":
            query_context = f" from '{last_query}'" if last_query else ""
            return f"I refined the existing results{query_context} because you added constraints instead of asking for a new search."
        if intent == "clarification_needed":
            return "I did not search again because the target product was ambiguous and needed clarification first."
        if intent == "general_question":
            return "I answered from conversation memory because the question was about the current thread, not about finding new products."
        if intent == "new_search":
            if last_query and normalize_text(last_query) != normalize_text(query):
                return "I started a fresh search because you asked for new products or changed the search direction."
            return "I started a fresh search because there was no stable selected-product context to continue from."
        return "I chose the next action from the current conversation context."

    def _attach_response_context(
        self,
        *,
        query: str,
        state: dict[str, Any],
        plan: dict[str, Any],
        response: dict[str, Any],
    ) -> dict[str, Any]:
        decorated = dict(response)
        assistant_context = self._build_assistant_context(query=query, state=state, plan=plan, response=response)
        decorated["intent"] = plan.get("intent")
        decorated["plan_summary"] = assistant_context.get("summary")
        decorated["assistant_context"] = assistant_context
        return decorated

    def _persist_assistant_response(
        self,
        *,
        conversation_id: str,
        user_id: str | None,
        query: str,
        state: dict[str, Any],
        plan: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        self.memory.append_turn(
            conversation_id=conversation_id,
            role="assistant",
            content=response["answer"],
            metadata={
                "mode": response.get("mode"),
                "intent": plan.get("intent"),
                "assistant_context": response.get("assistant_context"),
                "plan_summary": response.get("plan_summary"),
                "results_preview": self._compact_results(list(response.get("results") or [])),
                "tool_calls": len(response.get("tool_calls", [])),
                **({"fallback_reason": response.get("fallback_reason")} if response.get("fallback_reason") else {}),
            },
            user_id=user_id,
        )
        self.memory.update_state(conversation_id, self._build_state_patch(query=query, state=state, plan=plan, response=response))
        self._maybe_save_generated_report(
            conversation_id=conversation_id,
            user_id=user_id,
            query=query,
            plan=plan,
            response=response,
        )
        self.memory.refresh_summary(conversation_id)

    def _maybe_save_generated_report(
        self,
        *,
        conversation_id: str,
        user_id: str | None,
        query: str,
        plan: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        owner_user_id = str(user_id or "").strip()
        if not owner_user_id:
            return
        intent = str(plan.get("intent") or "")
        focus = str(plan.get("response_focus") or "")
        should_save = intent == "compare_products" or focus in {"sales", "seasonality", "reviews", "specs"}
        if not should_save:
            return
        results = self._compact_results(list(response.get("results") or []))
        payload = {
            "query": query,
            "filters": {"intent": intent, "focus": focus},
            "summary": {
                "result_count": len(results),
                "mode": response.get("mode"),
            },
            "products": results,
            "notes": [str(response.get("answer") or "").strip()],
        }
        try:
            report = save_report(
                settings=self.settings,
                db=self.db,
                owner_user_id=owner_user_id,
                report_type="assistant_report",
                title=f"Assistant report: {query[:80]}",
                payload=payload,
                conversation_id=conversation_id,
                source_kind="assistant",
            )
            self.memory.update_state(conversation_id, {"last_report_id": report["report_id"]})
            context = dict(response.get("assistant_context") or {})
            context["report_id"] = report["report_id"]
            response["assistant_context"] = context
        except Exception as exc:  # pragma: no cover
            logger.warning("assistant_report_save_failed conversation_id=%s error=%s", conversation_id, exc)

    @staticmethod
    def _merge_result_sets(local_rows: list[dict[str, Any]], live_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for source_rows in (local_rows, live_rows):
            for row in source_rows:
                if not isinstance(row, dict):
                    continue
                link = str(row.get("link") or row.get("external_url") or row.get("internal_path") or "").strip().lower()
                title = normalize_text(str(row.get("title") or ""))
                key = link or title
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(row)
        return merged

    def _schedule_background_online_search(
        self,
        *,
        conversation_id: str,
        user_id: str | None,
        query: str,
        local_results: list[dict[str, Any]],
        plan: dict[str, Any],
    ) -> None:
        if not self.settings.live_search_enabled or not self.settings.groq_api_key:
            self.memory.update_state(
                conversation_id,
                {
                    "search_status": "complete",
                    "search_phase": "complete",
                    "pending_online_refresh": False,
                    "online_results": [],
                    "merged_results": self._compact_results(local_results),
                },
            )
            return

        def worker() -> None:
            try:
                live_out = self.tools.call_tool(
                    "search_web_products",
                    {"query": query, "top_k": min(max(3, len(local_results) + 3), max(1, self.settings.live_search_max_results))},
                    context={"conversation_id": conversation_id, "user_id": user_id},
                )
                live_rows = (
                    filter_relevant_results(query, _normalize_live_offers(list((live_out.get("result") or {}).get("offers") or [])))
                    if live_out.get("ok")
                    else []
                )
                merged = self._merge_result_sets(local_results, live_rows)
                self.memory.update_state(
                    conversation_id,
                    {
                        "search_status": "complete",
                        "search_phase": "complete",
                        "pending_online_refresh": False,
                        "online_results": self._compact_results(live_rows),
                        "merged_results": self._compact_results(merged),
                        "online_completion_notice": f"Online search finished and merged {max(0, len(merged) - len(local_results))} additional result(s).",
                    },
                )
            except Exception as exc:
                self.memory.update_state(
                    conversation_id,
                    {
                        "search_status": "complete",
                        "search_phase": "complete",
                        "pending_online_refresh": False,
                        "online_results": [],
                        "merged_results": self._compact_results(local_results),
                        "online_completion_notice": f"Online search did not add more results: {exc}",
                    },
                )

        thread = threading.Thread(target=worker, name=f"assistant-online-{conversation_id}", daemon=True)
        thread.start()

    def _handle_local_first_new_search(
        self,
        *,
        query: str,
        conversation_id: str,
        user_id: str | None,
        top_k: int,
        include_tool_trace: bool,
        state: dict[str, Any],
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        local_results = filter_relevant_results(query, self.pipeline.search(query=query, top_k=top_k, user_id=user_id))
        relevance = _query_relevance_score(query, local_results)
        should_search_online = self.settings.live_search_enabled and bool(self.settings.groq_api_key)
        answer = self._build_grounded_answer(query=query, results=local_results)
        if local_results:
            answer = (
                "I found local marketplace results first and I am checking live web offers in the background.\n"
                + answer
                if should_search_online
                else answer
            )
        else:
            answer = (
                "I did not find strong local matches yet. I am checking live web offers now."
                if should_search_online
                else "I could not find matching local products."
            )
        response = {
            "conversation_id": conversation_id,
            "mode": "local_first_search",
            "answer": answer,
            "results": local_results,
            "search_phase": "local_partial" if should_search_online else "complete",
            "search_status": "online_searching" if should_search_online else "complete",
            "pending_online_refresh": should_search_online,
            "local_results": self._compact_results(local_results),
            "online_results": [],
            "merged_results": self._compact_results(local_results),
        }
        if include_tool_trace:
            response["tool_calls"] = [
                {
                    "tool_name": "search_offers_local",
                    "arguments": {"query": query, "top_k": top_k, "user_id": user_id},
                    "output": {"count": len(local_results), "relevance": round(float(relevance), 3)},
                }
            ]
        self.memory.update_state(
            conversation_id,
            {
                "search_status": response["search_status"],
                "search_phase": response["search_phase"],
                "pending_online_refresh": bool(response["pending_online_refresh"]),
                "local_results": self._compact_results(local_results),
                "online_results": [],
                "merged_results": self._compact_results(local_results),
                "online_completion_notice": None,
            },
        )
        if should_search_online:
            self._schedule_background_online_search(
                conversation_id=conversation_id,
                user_id=user_id,
                query=query,
                local_results=local_results,
                plan=plan,
            )
        return response

    def get_search_status(self, conversation_id: str, *, user_id: str | None = None) -> dict[str, Any]:
        self._enforce_conversation_owner(conversation_id=conversation_id, user_id=user_id, allow_missing_user=False)
        context = self.memory.get_context(conversation_id, 2)
        state = dict(context.get("state") or {})
        local_results = list(state.get("local_results") or [])
        online_results = list(state.get("online_results") or [])
        merged_results = list(state.get("merged_results") or state.get("last_results") or [])
        return {
            "conversation_id": conversation_id,
            "search_phase": state.get("search_phase") or "complete",
            "search_status": state.get("search_status") or "complete",
            "pending_online_refresh": bool(state.get("pending_online_refresh")),
            "local_results": local_results,
            "online_results": online_results,
            "merged_results": merged_results,
            "notice": state.get("online_completion_notice"),
            "report_id": state.get("last_report_id"),
        }

    @staticmethod
    def _clarification_response(
        *,
        conversation_id: str,
        question: str,
    ) -> dict[str, Any]:
        return {
            "conversation_id": conversation_id,
            "mode": "clarification_needed",
            "answer": question,
            "results": [],
        }

    def _handle_general_question(self, *, query: str, state: dict[str, Any], conversation_id: str) -> dict[str, Any]:
        normalized = normalize_text(query)
        active_offer = self._compact_offer(state.get("active_offer")) or self._compact_offer(state.get("last_reference_offer"))
        comparison: list[dict[str, Any]] = []
        for row in list(state.get("active_comparison_set") or []):
            compact = self._compact_offer(row)
            if compact is not None:
                comparison.append(compact)
        last_results = self._state_results(state)
        last_query = str(state.get("last_results_query") or "").strip()
        last_plan = dict(state.get("last_plan") or {})

        answer: str
        if "why" in normalized and last_plan:
            answer = self._build_plan_reason(query=query, state=state, plan=last_plan)
        elif ("comparing" in normalized or "compare" in normalized) and len(comparison) >= 2:
            names = [str(row.get("title") or "product").strip() for row in comparison[:2]]
            answer = f"We are currently comparing {names[0]} and {names[1]}."
        elif ("selected product" in normalized or "current product" in normalized or "discussing" in normalized) and active_offer:
            source = str(active_offer.get("source") or "store").strip()
            answer = f"The current selected product is {active_offer.get('title')} from {source}."
        elif "last search" in normalized and last_query:
            answer = f"The last search context was: {last_query}."
        elif "last results" in normalized and last_results:
            titles = [str(row.get("title") or "product").strip() for row in last_results[:3] if isinstance(row, dict)]
            answer = "The last result set included: " + ", ".join(titles) + "."
        elif active_offer:
            answer = f"We are currently focused on {active_offer.get('title')}."
        elif last_query:
            answer = f"The last search context was: {last_query}."
        elif last_results:
            answer = f"I still have the previous result set in context with {len(last_results)} items."
        else:
            answer = "There is no active product context yet. Start with a product search and I will keep the thread grounded from there."

        return {
            "conversation_id": conversation_id,
            "mode": "general_question",
            "answer": answer,
            "results": [],
        }

    def run(
        self,
        *,
        query: str,
        conversation_id: str | None = None,
        user_id: str | None = None,
        reference_product_id: str | None = None,
        top_k: int = 5,
        min_rating: float | None = None,
        include_tool_trace: bool = False,
    ) -> dict[str, Any]:
        q = query.strip()
        if len(q) < 2:
            raise ValueError("query must be at least 2 characters")

        if self.settings.conversation_require_user_id and not user_id:
            raise PermissionError("user_id is required for assistant conversation mode.")

        self._enforce_conversation_owner(conversation_id=conversation_id, user_id=user_id)

        conversation_id = self.memory.open_conversation(conversation_id, user_id=user_id)
        context = self.memory.get_context(conversation_id, self.settings.assistant_max_context_turns)
        state = context.get("state") or {}
        state = self._seed_reference_product_state(
            query=q,
            state=state,
            reference_product_id=reference_product_id,
        )
        context["state"] = state
        self.memory.append_turn(
            conversation_id=conversation_id,
            role="user",
            content=q,
            metadata={
                "top_k": top_k,
                "min_rating": min_rating,
                "reference_product_id": reference_product_id,
            },
            user_id=user_id,
        )
        plan = self._validate_turn_plan(
            query=q,
            state=state,
            plan=self._build_turn_plan(query=q, state=state, top_k=top_k, min_rating=min_rating),
        )
        reference_offer = plan.get("target_offer") if isinstance(plan.get("target_offer"), dict) else None

        if plan.get("intent") == "clarification_needed":
            clarification_response = self._clarification_response(
                conversation_id=conversation_id,
                question=str(plan.get("clarification_question") or "Which product do you want me to continue with?"),
            )
            clarification_response = self._attach_response_context(
                query=q,
                state=state,
                plan=plan,
                response=clarification_response,
            )
            self._persist_assistant_response(
                conversation_id=conversation_id,
                user_id=user_id,
                query=q,
                state=state,
                plan=plan,
                response=clarification_response,
            )
            return clarification_response

        if plan.get("intent") == "general_question":
            general_response = self._handle_general_question(query=q, state=state, conversation_id=conversation_id)
            general_response = self._attach_response_context(query=q, state=state, plan=plan, response=general_response)
            self._persist_assistant_response(
                conversation_id=conversation_id,
                user_id=user_id,
                query=q,
                state=state,
                plan=plan,
                response=general_response,
            )
            return general_response

        if plan.get("intent") == "selected_product_reviews" and reference_offer is not None:
            review_response = self._handle_review_followup(
                query=q,
                reference_offer=reference_offer,
                conversation_id=conversation_id,
                user_id=user_id,
                top_k=top_k,
                include_tool_trace=include_tool_trace,
            )
            review_response = self._attach_response_context(query=q, state=state, plan=plan, response=review_response)
            self._persist_assistant_response(
                conversation_id=conversation_id,
                user_id=user_id,
                query=q,
                state=state,
                plan=plan,
                response=review_response,
            )
            return review_response

        if plan.get("intent") in {"selected_product_followup", "selected_product_logistics", "selected_product_specs"} and reference_offer is not None:
            followup_response = self._handle_product_followup(
                query=q,
                reference_offer=reference_offer,
                conversation_id=conversation_id,
                user_id=user_id,
                include_tool_trace=include_tool_trace,
            )
            followup_response = self._attach_response_context(query=q, state=state, plan=plan, response=followup_response)
            self._persist_assistant_response(
                conversation_id=conversation_id,
                user_id=user_id,
                query=q,
                state=state,
                plan=plan,
                response=followup_response,
            )
            return followup_response

        if plan.get("intent") == "compare_products":
            compare_response = self._handle_compare_followup(
                query=q,
                comparison_offers=list(plan.get("comparison_offers") or []),
                conversation_id=conversation_id,
                user_id=user_id,
                include_tool_trace=include_tool_trace,
            )
            compare_response = self._attach_response_context(query=q, state=state, plan=plan, response=compare_response)
            self._persist_assistant_response(
                conversation_id=conversation_id,
                user_id=user_id,
                query=q,
                state=state,
                plan=plan,
                response=compare_response,
            )
            return compare_response

        if plan.get("intent") == "refine_previous_results":
            refine_response = self._handle_refine_results(query=q, state=state, conversation_id=conversation_id)
            refine_response = self._attach_response_context(query=q, state=state, plan=plan, response=refine_response)
            self._persist_assistant_response(
                conversation_id=conversation_id,
                user_id=user_id,
                query=q,
                state=state,
                plan=plan,
                response=refine_response,
            )
            return refine_response

        if plan.get("intent") == "new_search":
            local_first_response = self._handle_local_first_new_search(
                query=q,
                conversation_id=conversation_id,
                user_id=user_id,
                top_k=top_k,
                include_tool_trace=include_tool_trace,
                state=state,
                plan=plan,
            )
            local_first_response = self._attach_response_context(query=q, state=state, plan=plan, response=local_first_response)
            self._persist_assistant_response(
                conversation_id=conversation_id,
                user_id=user_id,
                query=q,
                state=state,
                plan=plan,
                response=local_first_response,
            )
            return local_first_response

        if not self.settings.llm_enabled or not self.settings.groq_api_key:
            fallback, fallback_trace = self._deterministic_fallback_with_trace(
                query=q,
                user_id=user_id,
                top_k=top_k,
                min_rating=min_rating,
            )
            response = {
                "conversation_id": conversation_id,
                "mode": "deterministic_fallback",
                **fallback,
            }
            if include_tool_trace:
                response["tool_calls"] = fallback_trace
            response = self._attach_response_context(query=q, state=state, plan=plan, response=response)
            self._persist_assistant_response(
                conversation_id=conversation_id,
                user_id=user_id,
                query=q,
                state=state,
                plan=plan,
                response=response,
            )
            return response

        try:
            result = self._llm_tool_loop(
                query=q,
                conversation_id=conversation_id,
                user_id=user_id,
                top_k=top_k,
                min_rating=min_rating,
                context=context,
                include_tool_trace=include_tool_trace,
                plan=plan,
            )
        except Exception as exc:
            logger.warning("assistant_llm_failed conversation_id=%s error=%s", conversation_id, exc)
            fallback, fallback_trace = self._deterministic_fallback_with_trace(
                query=q,
                user_id=user_id,
                top_k=top_k,
                min_rating=min_rating,
            )
            response = {
                "conversation_id": conversation_id,
                "mode": "deterministic_fallback",
                **fallback,
            }
            if include_tool_trace:
                response["tool_calls"] = fallback_trace
            response.setdefault("fallback_reason", "llm_failed_runtime")
            response = self._attach_response_context(query=q, state=state, plan=plan, response=response)
            self._persist_assistant_response(
                conversation_id=conversation_id,
                user_id=user_id,
                query=q,
                state=state,
                plan=plan,
                response=response,
            )
            return response
        result = self._attach_response_context(query=q, state=state, plan=plan, response=result)
        self._persist_assistant_response(
            conversation_id=conversation_id,
            user_id=user_id,
            query=q,
            state=state,
            plan=plan,
            response=result,
        )
        return result

    def _seed_reference_product_state(
        self,
        *,
        query: str,
        state: dict[str, Any],
        reference_product_id: str | None,
    ) -> dict[str, Any]:
        product_id = str(reference_product_id or "").strip()
        if not product_id or _is_new_search_query(query):
            return state
        offer = lookup_store_product_offer(db=self.db, settings=self.settings, product_id=product_id)
        compact = self._compact_offer(offer)
        if compact is None:
            return state
        seeded = dict(state)
        seeded["active_offer"] = compact
        seeded["last_reference_offer"] = compact
        seeded["active_offer_details"] = compact
        seeded["last_results"] = [compact]
        seeded["last_results_query"] = str(compact.get("title") or query).strip() or query
        return seeded

    def get_history(self, conversation_id: str, limit: int = 100, user_id: str | None = None) -> dict[str, Any]:
        self._enforce_conversation_owner(conversation_id=conversation_id, user_id=user_id, allow_missing_user=False)
        return {
            "conversation_id": conversation_id,
            "count": max(0, limit),
            "turns": self.memory.get_history(conversation_id, limit=limit),
        }

    def delete_conversation(self, conversation_id: str, user_id: str | None = None) -> dict[str, Any]:
        self._enforce_conversation_owner(conversation_id=conversation_id, user_id=user_id, allow_missing_user=False)
        stats = self.memory.delete_conversation(conversation_id)
        tool_logs_deleted = self.db[self.settings.assistant_tool_logs_collection].delete_many(
            {"conversation_id": conversation_id}
        ).deleted_count
        stats["tool_logs_deleted"] = int(tool_logs_deleted)
        return stats

    def _enforce_conversation_owner(
        self,
        *,
        conversation_id: str | None,
        user_id: str | None,
        allow_missing_user: bool = True,
    ) -> None:
        if not conversation_id:
            if self.settings.conversation_require_user_id and not user_id and not allow_missing_user:
                raise PermissionError("user_id is required for conversation access.")
            return
        session = self.memory.get_session(conversation_id)
        if not session:
            if self.settings.conversation_require_user_id and not user_id and not allow_missing_user:
                raise PermissionError("user_id is required for conversation access.")
            return
        owner = session.get("user_id")
        if owner:
            if not user_id:
                raise PermissionError("user_id is required for this conversation.")
            if str(user_id) != str(owner):
                raise PermissionError("conversation_access_denied: owner mismatch.")
        elif self.settings.conversation_require_user_id and not user_id and not allow_missing_user:
            raise PermissionError("user_id is required for conversation access.")

    def _llm_tool_loop(
        self,
        *,
        query: str,
        conversation_id: str,
        user_id: str | None,
        top_k: int,
        min_rating: float | None,
        context: dict[str, Any],
        include_tool_trace: bool,
        plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        tool_specs = self.tools.list_tools()
        state = context.get("state") or {}
        reference_offer = plan.get("target_offer") if isinstance(plan, dict) and isinstance(plan.get("target_offer"), dict) else None
        if reference_offer is None:
            reference_offer = self._resolve_reference_offer(query=query, state=state)
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    SYSTEM_PROMPT
                    + "\n\nAvailable tools JSON:\n"
                    + json.dumps(tool_specs, ensure_ascii=True)
                    + "\n\nCurrent request defaults:\n"
                    + json.dumps(
                        {"top_k": top_k, "min_rating": min_rating, "user_id": user_id},
                        ensure_ascii=True,
                    )
                ),
            }
        ]
        if isinstance(plan, dict):
            messages.append(
                {
                    "role": "system",
                    "content": "Current routing plan:\n" + json.dumps(_to_jsonable(plan), ensure_ascii=True),
                }
            )
        if reference_offer is not None:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Current follow-up reference offer:\n"
                        + json.dumps(reference_offer, ensure_ascii=True)
                        + "\nIf user asks about 'this/that/it', use this offer context."
                    ),
                }
            )
        summary = str(context.get("summary") or "").strip()
        if summary:
            messages.append({"role": "system", "content": summary})
        for row in context.get("turns", []):
            role = row.get("role")
            content = str(row.get("content", "")).strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": query})

        max_calls = max(1, self.settings.assistant_max_tool_calls)
        max_tool_payload = max(600, self.settings.assistant_max_tool_output_chars)

        tool_trace: list[dict[str, Any]] = []
        latest_results: list[dict[str, Any]] = []
        parse_failures = 0
        final_answer: str | None = None
        fallback_reason: str | None = None

        for _ in range(max_calls + 1):
            response_text = self._chat(messages)
            try:
                payload = _extract_json_payload(response_text)
            except Exception:
                parse_failures += 1
                logger.warning("assistant_json_parse_failed conversation_id=%s", conversation_id)
                if parse_failures >= 2:
                    break
                messages.append({"role": "assistant", "content": response_text})
                messages.append(
                    {
                        "role": "user",
                        "content": 'Return only valid JSON following the required schema with key "action".',
                    }
                )
                continue

            action = str(payload.get("action", "")).strip().lower()
            if action == "final":
                final_answer = str(payload.get("answer", "")).strip()
                if final_answer:
                    break
                messages.append({"role": "assistant", "content": response_text})
                messages.append({"role": "user", "content": 'Field "answer" was empty. Return a non-empty final answer.'})
                continue

            if action != "tool":
                messages.append({"role": "assistant", "content": response_text})
                messages.append(
                    {
                        "role": "user",
                        "content": 'Unknown action. Return either {"action":"tool",...} or {"action":"final",...}.',
                    }
                )
                continue

            tool_name = str(payload.get("tool_name", "")).strip()
            raw_args = payload.get("arguments", {})
            if not isinstance(raw_args, dict):
                raw_args = {}
            tool_args = dict(raw_args)
            has_search_context = bool(latest_results) or reference_offer is not None
            if not self._is_tool_allowed_for_plan(plan, tool_name, has_search_context=has_search_context):
                messages.append({"role": "assistant", "content": response_text})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Tool '{tool_name}' is not allowed for the current routing plan. "
                            "Follow the routing plan, use only allowed tools, or return a final answer."
                        ),
                    }
                )
                continue
            if tool_name == "search_offers":
                tool_args.setdefault("query", query)
                tool_args.setdefault("top_k", top_k)
                if min_rating is not None:
                    tool_args.setdefault("min_rating", min_rating)
                if user_id:
                    tool_args.setdefault("user_id", user_id)
            elif tool_name == "search_web_products":
                tool_args.setdefault("query", query)
                tool_args.setdefault("top_k", min(top_k, 10))
            elif tool_name == "inspect_product_page" and reference_offer is not None:
                tool_args.setdefault("link", reference_offer.get("link"))
                if reference_offer.get("source"):
                    tool_args.setdefault("source", reference_offer.get("source"))
                if reference_offer.get("title"):
                    tool_args.setdefault("title_hint", reference_offer.get("title"))
            elif tool_name == "log_interaction" and user_id:
                tool_args.setdefault("user_id", user_id)

            context_payload = {"conversation_id": conversation_id, "user_id": user_id}
            try:
                tool_output = self.tools.call_tool(tool_name, tool_args, context=context_payload)
            except MCPToolError as exc:
                tool_output = {
                    "ok": False,
                    "tool_name": tool_name,
                    "error": str(exc),
                }
            except Exception as exc:  # pragma: no cover - defensive fallback
                logger.exception("assistant_tool_call_failed conversation_id=%s tool=%s", conversation_id, tool_name)
                tool_output = {
                    "ok": False,
                    "tool_name": tool_name,
                    "error": f"tool_execution_failed:{exc}",
                }

            serialized_output = json.dumps(_to_jsonable(tool_output), ensure_ascii=True)
            if len(serialized_output) > max_tool_payload:
                serialized_output = serialized_output[:max_tool_payload] + "...<truncated>"

            trace_item = {
                "tool_name": tool_name,
                "arguments": _to_jsonable(tool_args),
                "output": _to_jsonable(tool_output),
            }
            tool_trace.append(trace_item)
            if tool_name == "search_offers" and tool_output.get("ok"):
                search_rows = filter_relevant_results(query, list((tool_output.get("result") or {}).get("results") or []))
                latest_results = search_rows
                relevance = _query_relevance_score(query, search_rows)
                should_auto_live = (not search_rows) or (relevance < 0.25)
                if should_auto_live and self.settings.live_search_enabled and self.settings.groq_api_key:
                    try:
                        live_args = {"query": query, "top_k": min(top_k, max(1, self.settings.live_search_max_results))}
                        live_out = self.tools.call_tool("search_web_products", live_args, context=context_payload)
                        live_serialized = json.dumps(_to_jsonable(live_out), ensure_ascii=True)
                        if len(live_serialized) > max_tool_payload:
                            live_serialized = live_serialized[:max_tool_payload] + "...<truncated>"
                        tool_trace.append(
                            {
                                "tool_name": "search_web_products",
                                "arguments": _to_jsonable(live_args),
                                "output": _to_jsonable(live_out),
                            }
                        )
                        if live_out.get("ok"):
                            latest_results = filter_relevant_results(
                                query,
                                _normalize_live_offers(list((live_out.get("result") or {}).get("offers") or [])),
                            )
                        self.memory.append_turn(
                            conversation_id=conversation_id,
                            role="tool",
                            content=live_serialized,
                            tool_name="search_web_products",
                            metadata={
                                "arguments": _to_jsonable(live_args),
                                "trigger": "auto_after_weak_or_empty_search_offers",
                                "local_relevance_score": relevance,
                            },
                            user_id=user_id,
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "AUTO_TOOL_RESULT search_web_products triggered due to empty local results:\n"
                                    f"{live_serialized}\n"
                                    'If enough info is available, return {"action":"final",...}.'
                                ),
                            }
                        )
                    except Exception as exc:
                        logger.warning("auto_live_search_failed conversation_id=%s error=%s", conversation_id, exc)
            elif tool_name == "search_web_products" and tool_output.get("ok"):
                latest_results = filter_relevant_results(
                    query,
                    _normalize_live_offers(list((tool_output.get("result") or {}).get("offers") or [])),
                )
            elif tool_name == "inspect_product_page" and tool_output.get("ok"):
                info = (tool_output.get("result") or {})
                title_value = info.get("title")
                if not title_value and reference_offer is not None:
                    title_value = reference_offer.get("title")
                source_value = info.get("source")
                if not source_value and reference_offer is not None:
                    source_value = reference_offer.get("source")
                latest_results = [
                    {
                        "title": title_value,
                        "link": info.get("final_url") or info.get("link"),
                        "source": source_value or "web",
                        "image": info.get("image") or (reference_offer.get("image") if reference_offer else None),
                        "price_pkr": info.get("price_pkr"),
                        "total_price_pkr": info.get("price_pkr"),
                        "rating": info.get("rating"),
                        "review_count": info.get("review_count"),
                        "match_score": None,
                        "rank_score": None,
                        "reason": "Direct page inspection for reviews/ratings.",
                    }
                ]

            self.memory.append_turn(
                conversation_id=conversation_id,
                role="tool",
                content=serialized_output,
                tool_name=tool_name,
                metadata={"arguments": _to_jsonable(tool_args)},
                user_id=user_id,
            )
            messages.append({"role": "assistant", "content": json.dumps(payload, ensure_ascii=True)})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"TOOL_RESULT {tool_name}:\n{serialized_output}\n"
                        'If enough info is available, return {"action":"final",...}.'
                    ),
                }
            )

        if not final_answer:
            fallback, fallback_trace = self._deterministic_fallback_with_trace(
                query=query,
                user_id=user_id,
                top_k=top_k,
                min_rating=min_rating,
            )
            fallback_reason = str(fallback.get("fallback_reason") or "llm_no_final_answer")
            if fallback_trace:
                tool_trace.extend(fallback_trace)
            response = {
                "conversation_id": conversation_id,
                "mode": "deterministic_fallback",
                "answer": fallback["answer"],
                "results": fallback["results"],
                "fallback_reason": fallback_reason,
            }
            if include_tool_trace:
                response["tool_calls"] = tool_trace
            return response

        output_results = latest_results
        response_mode = "tool_calling"
        if not output_results:
            fallback, fallback_trace = self._deterministic_fallback_with_trace(
                query=query,
                user_id=user_id,
                top_k=top_k,
                min_rating=min_rating,
            )
            if fallback_trace:
                tool_trace.extend(fallback_trace)
            output_results = fallback["results"]
            final_answer = fallback["answer"]
            fallback_reason = str(fallback.get("fallback_reason") or "tool_loop_no_results")
            response_mode = "tool_calling_fallback"
        else:
            final_answer = self._build_grounded_answer(query=query, results=output_results, fallback_text=final_answer)

        response = {
            "conversation_id": conversation_id,
            "mode": response_mode,
            "answer": final_answer,
            "results": output_results,
        }
        if fallback_reason:
            response["fallback_reason"] = fallback_reason
        if include_tool_trace:
            response["tool_calls"] = tool_trace
        return response

    def _chat(self, messages: list[dict[str, str]]) -> str:
        from groq import Groq  # type: ignore

        client = Groq(api_key=self.settings.groq_api_key)
        for attempt in range(2):
            req_messages = messages
            if attempt == 1:
                req_messages = messages + [{"role": "user", "content": "Return exactly one valid JSON object now."}]
            response = client.chat.completions.create(
                model=self.settings.assistant_model,
                messages=req_messages,
                temperature=0,
                max_tokens=700,
            )
            text = (response.choices[0].message.content or "").strip()
            if text:
                return text
            logger.warning(
                "assistant_empty_model_response model=%s attempt=%s",
                self.settings.assistant_model,
                attempt + 1,
            )
        raise RuntimeError("Empty model response after retries")

    def _deterministic_fallback(
        self, *, query: str, user_id: str | None, top_k: int, min_rating: float | None
    ) -> dict[str, Any]:
        fallback, _ = self._deterministic_fallback_with_trace(
            query=query,
            user_id=user_id,
            top_k=top_k,
            min_rating=min_rating,
        )
        return fallback

    def _deterministic_fallback_with_trace(
        self, *, query: str, user_id: str | None, top_k: int, min_rating: float | None
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        trace: list[dict[str, Any]] = []
        effective_query = query if min_rating is None else f"{query} {min_rating}+ stars high rating low price"
        results = filter_relevant_results(query, self.pipeline.search(query=effective_query, top_k=top_k, user_id=user_id))
        if min_rating is not None:
            results = [r for r in results if r.get("rating") is not None and float(r["rating"]) >= float(min_rating)]

        relevance = _query_relevance_score(query, results)
        trace.append(
            {
                "tool_name": "search_offers_local",
                "arguments": _to_jsonable({"query": effective_query, "top_k": top_k, "user_id": user_id}),
                "output": {"count": len(results), "relevance": round(float(relevance), 3)},
                "trigger": "deterministic_fallback_local_search",
            }
        )
        if (not results) or (relevance < 0.25):
            live_results: list[dict[str, Any]] = []
            if self.settings.live_search_enabled and self.settings.groq_api_key:
                try:
                    live_args = {"query": query, "top_k": min(top_k, max(1, self.settings.live_search_max_results))}
                    live_out = self.tools.call_tool(
                        "search_web_products",
                        live_args,
                        context={"user_id": user_id},
                    )
                    trace.append(
                        {
                            "tool_name": "search_web_products",
                            "arguments": _to_jsonable(live_args),
                            "output": _to_jsonable(live_out),
                            "trigger": "deterministic_fallback_live_search",
                        }
                    )
                    if live_out.get("ok"):
                        live_results = filter_relevant_results(
                            query,
                            _normalize_live_offers(list((live_out.get("result") or {}).get("offers") or [])),
                        )
                except Exception as exc:
                    logger.warning("fallback_live_search_failed error=%s", exc)
                    trace.append(
                        {
                            "tool_name": "search_web_products",
                            "arguments": {"query": query},
                            "output": {"ok": False, "error": str(exc)},
                            "trigger": "deterministic_fallback_live_search",
                        }
                    )
            if live_results:
                best_live = live_results[0]
                title_live = str(best_live.get("title", "Best live match"))
                source_live = str(best_live.get("source", "web"))
                price_live = _fmt_price(best_live.get("price_pkr"))
                rating_live = best_live.get("rating")
                rating_live_txt = "N/A" if rating_live is None else f"{float(rating_live):.1f}"
                answer = (
                    f"I used live web search for better coverage and found: "
                    f"{title_live} from {source_live} at PKR {price_live} (rating {rating_live_txt})."
                )
                return {
                    "answer": answer,
                    "results": live_results,
                    "fallback_reason": "used_live_search_due_to_weak_local_relevance",
                }, trace

            answer = "I could not find matching offers in local data or live web search. Try broader keywords or a higher budget."
            return {"answer": answer, "results": [], "fallback_reason": "no_results_local_or_live"}, trace

        best = results[0]
        title = str(best.get("title", "Best match"))
        source = str(best.get("source", "unknown"))
        price = _fmt_price(best.get("total_price_pkr") or best.get("price_pkr"))
        rating = best.get("rating")
        rating_txt = "N/A" if rating is None else f"{float(rating):.1f}"
        answer = (
            f"Best current option: {title} from {source} at PKR {price} (rating {rating_txt}). "
            f"I found {len(results)} strong alternatives as well."
        )
        return {"answer": answer, "results": results, "fallback_reason": "local_search_sufficient"}, trace

    @staticmethod
    def _compact_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for row in results[:10]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title", "")).strip()
            link = str(row.get("link", "")).strip()
            if not title or not link:
                continue
            compact.append(
                {
                    "offer_id": row.get("offer_id"),
                    "title": title,
                    "link": link,
                    "source": str(row.get("source", "web")).strip().lower() or "web",
                    "image": row.get("image"),
                    "price_pkr": row.get("price_pkr"),
                    "total_price_pkr": row.get("total_price_pkr") if row.get("total_price_pkr") is not None else row.get("price_pkr"),
                    "rating": row.get("rating"),
                    "review_count": row.get("review_count"),
                    "source_rating": row.get("source_rating"),
                    "source_review_count": row.get("source_review_count"),
                    "display_source_rating": row.get("display_source_rating"),
                    "display_source_review_count": row.get("display_source_review_count"),
                    "display_source_rating_kind": row.get("display_source_rating_kind"),
                    "price_range_pkr_min": row.get("price_range_pkr_min"),
                    "price_range_pkr_max": row.get("price_range_pkr_max"),
                    "availability": row.get("availability"),
                    "in_stock": row.get("in_stock"),
                    "brand": row.get("brand"),
                    "model": row.get("model"),
                    "category": row.get("category"),
                    "subcategory": row.get("subcategory"),
                    "specifications": row.get("specifications"),
                    "shipping_pkr": row.get("shipping_pkr"),
                    "shipping_price_pkr": row.get("shipping_price_pkr"),
                    "shipping_summary": row.get("shipping_summary"),
                    "delivery_info": row.get("delivery_info"),
                    "warranty_info": row.get("warranty_info"),
                    "predicted_app_rating": row.get("predicted_app_rating"),
                }
            )
        return compact

    def _resolve_reference_offer(self, *, query: str, state: dict[str, Any]) -> dict[str, Any] | None:
        if _is_new_search_query(query):
            return None
        explicit = None
        rows = None
        if isinstance(state, dict):
            explicit = state.get("active_offer") or state.get("last_reference_offer")
            rows = state.get("last_results")
            if not isinstance(rows, list) or not rows:
                rows = state.get("last_search_results")
        if not isinstance(rows, list) or not rows:
            if isinstance(explicit, dict) and explicit.get("link") and (
                _is_reference_query(query) or _is_product_followup_query(query)
            ):
                return explicit
            return None
        normalized_query = normalize_text(query)
        ordinal_map = {
            "first": 0,
            "1st": 0,
            "second": 1,
            "2nd": 1,
            "third": 2,
            "3rd": 2,
            "fourth": 3,
            "4th": 3,
            "fifth": 4,
            "5th": 4,
        }
        for token, idx in ordinal_map.items():
            if token in normalized_query and idx < len(rows) and isinstance(rows[idx], dict):
                return rows[idx]

        numeric_patterns = (
            r"(?:product|item|option|offer|listing|result)\s*(?:no|number|#)?\s*(\d{1,2})\b",
            r"(?:no|number|#)\s*(\d{1,2})\b",
            r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:product|item|option|offer|listing|result|one)\b",
        )
        for pattern in numeric_patterns:
            match = re.search(pattern, normalized_query)
            if not match:
                continue
            idx = int(match.group(1)) - 1
            if 0 <= idx < len(rows) and isinstance(rows[idx], dict):
                return rows[idx]

        tokens = set(normalized_query.split())
        meaningful_tokens = {t for t in tokens if t not in REFERENCE_STOPWORDS}
        meaningful_variants = _token_variants(meaningful_tokens)

        if meaningful_variants and _is_product_followup_query(query):
            best_idx = -1
            best_distinctive_hits = 0
            best_total_hits = 0
            for idx, row in enumerate(rows[:10]):
                if not isinstance(row, dict):
                    continue
                title_tokens = set(normalize_text(str(row.get("title", ""))).split())
                title_variants = _token_variants(title_tokens)
                hits = meaningful_variants.intersection(title_variants)
                if not hits:
                    continue
                distinctive_hits = sum(1 for token in hits if _is_distinctive_reference_token(token))
                total_hits = len(hits)
                if distinctive_hits > best_distinctive_hits or (
                    distinctive_hits == best_distinctive_hits and total_hits > best_total_hits
                ):
                    best_idx = idx
                    best_distinctive_hits = distinctive_hits
                    best_total_hits = total_hits
            if best_idx >= 0 and (best_distinctive_hits > 0 or best_total_hits >= 2):
                return rows[best_idx] if isinstance(rows[best_idx], dict) else None

        best_idx = -1
        best_score = 0.0
        for idx, row in enumerate(rows[:10]):
            if not isinstance(row, dict):
                continue
            title_tokens = set(normalize_text(str(row.get("title", ""))).split())
            if not title_tokens:
                continue
            title_variants = _token_variants(title_tokens)
            overlap = len(meaningful_variants.intersection(title_variants))
            score = overlap / max(1, min(len(meaningful_variants), len(title_variants)))
            if score > best_score:
                best_score = score
                best_idx = idx
        threshold = 0.25 if _is_product_followup_query(query) else 0.35
        if best_idx >= 0 and best_score >= threshold and isinstance(rows[best_idx], dict):
            return rows[best_idx]

        if isinstance(explicit, dict) and explicit.get("link") and _is_product_followup_query(query):
            return explicit

        if _is_reference_query(query):
            if isinstance(explicit, dict) and explicit.get("link"):
                return explicit
            if len(rows) == 1 and isinstance(rows[0], dict):
                return rows[0]
            return None
        return None

    def _handle_product_followup(
        self,
        *,
        query: str,
        reference_offer: dict[str, Any],
        conversation_id: str,
        user_id: str | None,
        include_tool_trace: bool,
    ) -> dict[str, Any]:
        trace: list[dict[str, Any]] = []
        context_payload = {"conversation_id": conversation_id, "user_id": user_id}

        detail_args: dict[str, Any] = {}
        if reference_offer.get("offer_id"):
            detail_args["offer_id"] = reference_offer.get("offer_id")
        else:
            if reference_offer.get("source"):
                detail_args["source"] = reference_offer.get("source")
            if reference_offer.get("link"):
                detail_args["link"] = reference_offer.get("link")

        detail_out: dict[str, Any]
        if detail_args:
            try:
                detail_out = self.tools.call_tool("get_offer_details", detail_args, context=context_payload)
            except Exception as exc:
                detail_out = {"ok": False, "tool_name": "get_offer_details", "error": str(exc), "result": {}}
            trace.append({"tool_name": "get_offer_details", "arguments": detail_args, "output": _to_jsonable(detail_out)})
        else:
            detail_out = {"ok": False, "tool_name": "get_offer_details", "error": "missing_offer_identifier", "result": {}}

        inspect_args = {"link": reference_offer.get("link")}
        if reference_offer.get("source"):
            inspect_args["source"] = reference_offer.get("source")
        if reference_offer.get("title"):
            inspect_args["title_hint"] = reference_offer.get("title")
        try:
            inspect_out = self.tools.call_tool("inspect_product_page", inspect_args, context=context_payload)
        except Exception as exc:
            inspect_out = {"ok": False, "tool_name": "inspect_product_page", "error": str(exc), "result": {}}
        trace.append({"tool_name": "inspect_product_page", "arguments": inspect_args, "output": _to_jsonable(inspect_out)})

        result: dict[str, Any] = dict(reference_offer)
        detail_result = (detail_out.get("result") or {}) if detail_out.get("ok") else {}
        offer_doc = detail_result.get("offer") if isinstance(detail_result, dict) else None
        if isinstance(offer_doc, dict):
            for field in (
                "offer_id",
                "title",
                "title_normalized",
                "link",
                "source",
                "image",
                "price_pkr",
                "shipping_pkr",
                "in_stock",
                "rating",
                "review_count",
                "brand",
                "model",
                "storage_gb",
                "ram_gb",
                "category",
                "subcategory",
                "specifications",
                "last_scraped",
                "last_seen_at",
            ):
                if field in offer_doc and offer_doc.get(field) not in (None, "", []):
                    result[field] = offer_doc.get(field)

        inspected = (inspect_out.get("result") or {}) if inspect_out.get("ok") else {}
        inspected_page_type = str(inspected.get("page_type") or "").strip().lower()
        inspected_is_listing = inspected_page_type == "listing"
        inspection_warning = str(inspected.get("page_warning") or "").strip() if inspected_is_listing else ""
        if isinstance(inspected, dict) and inspected:
            if inspected.get("title") and not result.get("title"):
                result["title"] = inspected.get("title")
            if inspected.get("final_url"):
                result["link"] = inspected.get("final_url")
            if inspected.get("source") and not result.get("source"):
                result["source"] = inspected.get("source")
            for field in ("image", "rating", "review_count", "price_pkr", "availability"):
                if inspected_is_listing and field in {"rating", "review_count", "price_pkr", "availability"}:
                    continue
                if inspected.get(field) not in (None, "", []):
                    if field == "price_pkr" and not _should_use_inspected_price(result.get("price_pkr"), inspected.get(field)):
                        continue
                    result[field] = inspected.get(field)
            inspected_shipping = inspected.get("shipping_price_pkr")
            if not inspected_is_listing and inspected_shipping not in (None, ""):
                result["shipping_price_pkr"] = inspected_shipping
                result["shipping_pkr"] = inspected_shipping
            if not inspected_is_listing:
                for field in ("shipping_summary", "delivery_info", "warranty_info"):
                    if inspected.get(field) not in (None, "", []):
                        result[field] = inspected.get(field)
                if inspected.get("review_snippets"):
                    result["review_snippets"] = inspected.get("review_snippets")
                if inspected.get("has_review_signals") is not None:
                    result["has_review_signals"] = inspected.get("has_review_signals")
            if inspection_warning:
                result["inspection_warning"] = inspection_warning

        price = result.get("price_pkr")
        shipping = result.get("shipping_pkr") or 0.0
        try:
            result["total_price_pkr"] = None if price is None else float(price) + float(shipping)
        except (TypeError, ValueError):
            result["total_price_pkr"] = price
        result["reason"] = "Follow-up details for the selected prior result."

        title = str(result.get("title") or reference_offer.get("title") or "selected product")
        source = str(result.get("source") or reference_offer.get("source") or "store")
        rating = result.get("rating")
        try:
            rating_txt = "N/A" if rating is None else f"{float(rating):.1f}"
        except (TypeError, ValueError):
            rating_txt = str(rating)
        review_count = result.get("review_count")
        review_txt = "N/A" if review_count in (None, "") else str(review_count)
        availability = result.get("availability")
        if not availability and result.get("in_stock") is not None:
            availability = "in stock" if result.get("in_stock") else "out of stock"
        shipping_price_explicit = _safe_float(result.get("shipping_price_pkr"))
        shipping_price_saved = _safe_float(result.get("shipping_pkr"))
        shipping_price = shipping_price_explicit
        if shipping_price is None and shipping_price_saved is not None and shipping_price_saved > 0:
            shipping_price = shipping_price_saved
        shipping_summary = str(result.get("shipping_summary") or "").strip()
        delivery_info = str(result.get("delivery_info") or "").strip()
        warranty_info = str(result.get("warranty_info") or "").strip()
        specs = str(result.get("specifications") or "").strip()
        if len(specs) > 260:
            specs = specs[:257].rstrip() + "..."
        units_sold = _safe_int(result.get("units_sold"))
        order_count = _safe_int(result.get("order_count"))
        revenue_pkr = _safe_float(result.get("revenue_pkr"))
        predicted_app_rating = _safe_float(result.get("predicted_app_rating"))
        predicted_demand_score = _safe_float(result.get("predicted_demand_score"))
        seasonal_relevance_score = _safe_float(result.get("seasonal_relevance_score"))
        best_month_labels = result.get("best_month_labels") if isinstance(result.get("best_month_labels"), list) else []
        price_range_txt = _format_price_range(result)
        display_rating, display_rating_kind = _display_rating_value(result)
        link = str(result.get("link") or reference_offer.get("link") or "").strip()

        snippets = result.get("review_snippets") if isinstance(result.get("review_snippets"), list) else []
        page_checked = bool(inspected) and inspect_out.get("ok") is not False

        if _is_delivery_query(query):
            lines = [f"Delivery info for {title} ({source}):"]
            if shipping_price is not None:
                if shipping_price <= 0:
                    lines.append("The page indicates free delivery/shipping.")
                else:
                    lines.append(f"Delivery/shipping charge: PKR {_fmt_price(shipping_price)}.")
            elif shipping_summary:
                lines.append(_punctuate_line(shipping_summary))
            else:
                lines.append("I checked the product page but could not find an explicit delivery/shipping charge.")
            if delivery_info and delivery_info.lower() != shipping_summary.lower():
                lines.append(_punctuate_line(delivery_info))
            lines.append(f"Product price: PKR {_fmt_price(result.get('price_pkr'))}.")
            if price_range_txt:
                lines.append(f"Price range across matching offers: {price_range_txt}.")
            if link:
                lines.append(f"Link: {link}")
        elif _is_warranty_query(query):
            lines = [f"Warranty info for {title} ({source}):"]
            if warranty_info:
                lines.append(_punctuate_line(warranty_info))
            else:
                lines.append("I checked the product page but could not find an explicit warranty statement.")
            if availability:
                lines.append(f"Availability: {availability}.")
            if link:
                lines.append(f"Link: {link}")
        elif _is_availability_query(query):
            lines = [f"Availability for {title} ({source}):"]
            if availability:
                lines.append(f"Status: {availability}.")
            else:
                lines.append("I checked the product page but could not confirm stock/availability from this pass.")
            if shipping_summary:
                lines.append(_punctuate_line(shipping_summary))
            if link:
                lines.append(f"Link: {link}")
        elif _is_price_query(query) and not _is_delivery_query(query):
            lines = [
                f"Price info for {title} ({source}):",
                f"Current product price: PKR {_fmt_price(result.get('price_pkr'))}.",
            ]
            if price_range_txt:
                lines.append(f"Price range across matching offers: {price_range_txt}.")
            if shipping_price is not None:
                if shipping_price <= 0:
                    lines.append("Delivery appears to be free.")
                else:
                    lines.append(f"Delivery/shipping charge: PKR {_fmt_price(shipping_price)}.")
                lines.append(f"Estimated total: PKR {_fmt_price(result.get('total_price_pkr'))}.")
            if link:
                lines.append(f"Link: {link}")
        elif _is_specs_query(query):
            lines = [f"Specs for {title} ({source}):"]
            if specs:
                lines.append(f"Specs: {specs}")
            else:
                lines.append("I could not find detailed specifications in the saved listing or inspected page.")
            if warranty_info:
                lines.append(_punctuate_line(f"Warranty: {warranty_info}"))
            if link:
                lines.append(f"Link: {link}")
        elif _is_sales_query(query):
            lines = [f"Sales report for {title} ({source}):"]
            lines.append(f"Units sold locally: {units_sold if units_sold is not None else 0}.")
            lines.append(f"Completed order count: {order_count if order_count is not None else 0}.")
            if revenue_pkr is not None:
                lines.append(f"Revenue: PKR {_fmt_price(revenue_pkr)}.")
            if predicted_demand_score is not None:
                lines.append(f"Predicted demand score: {predicted_demand_score:.2f}.")
            if predicted_app_rating is not None:
                lines.append(f"Predicted local app rating: {predicted_app_rating:.1f}.")
            if link:
                lines.append(f"Link: {link}")
        elif _is_seasonality_query(query):
            lines = [f"Seasonality for {title} ({source}):"]
            if best_month_labels:
                lines.append(f"Best months: {', '.join(str(x) for x in best_month_labels[:4])}.")
            else:
                lines.append("I do not have a strong month recommendation yet from observed data.")
            if seasonal_relevance_score is not None:
                lines.append(f"Seasonal relevance score: {seasonal_relevance_score:.2f}.")
            if predicted_demand_score is not None:
                lines.append(f"Predicted demand score: {predicted_demand_score:.2f}.")
            if predicted_app_rating is not None:
                lines.append(f"Predicted local app rating: {predicted_app_rating:.1f}.")
            if link:
                lines.append(f"Link: {link}")
        else:
            rating_line = (
                f"Rating: {display_rating:.1f}; reviews: {review_txt}."
                if display_rating is not None
                else f"Rating: {rating_txt}; reviews: {review_txt}."
            )
            lines = [
                f"Details for {title} ({source}):",
                f"Price: PKR {_fmt_price(result.get('total_price_pkr') or result.get('price_pkr'))}.",
                rating_line,
            ]
            if display_rating_kind == "predicted":
                lines.append("Displayed rating is a predicted fallback because scraped source-site rating is missing.")
            if price_range_txt:
                lines.append(f"Price range across matching offers: {price_range_txt}.")
            if availability:
                lines.append(f"Availability: {availability}.")
            if shipping_price is not None:
                if shipping_price <= 0:
                    lines.append("Delivery: free shipping.")
                else:
                    lines.append(f"Delivery/shipping charge: PKR {_fmt_price(shipping_price)}.")
            elif shipping_summary:
                lines.append(_punctuate_line(f"Delivery: {shipping_summary}"))
            if warranty_info:
                lines.append(_punctuate_line(f"Warranty: {warranty_info}"))
            if specs:
                lines.append(f"Specs: {specs}")
            if units_sold is not None or order_count is not None:
                lines.append(
                    f"Local sales: {units_sold if units_sold is not None else 0} units, {order_count if order_count is not None else 0} completed orders."
                )
            if predicted_demand_score is not None:
                lines.append(f"Predicted demand score: {predicted_demand_score:.2f}.")
            if predicted_app_rating is not None:
                lines.append(f"Predicted app rating: {predicted_app_rating:.1f}.")
            if best_month_labels:
                lines.append(f"Best months: {', '.join(str(x) for x in best_month_labels[:4])}.")
            if snippets:
                lines.append(f"Sample review: {str(snippets[0])[:220]}")
            if link:
                lines.append(f"Link: {link}")

        if inspect_out.get("ok") is False:
            lines.append("I used the saved listing data because the product page could not be inspected in this pass.")
        elif inspection_warning:
            lines.append(inspection_warning)
        elif page_checked:
            lines.append("I checked the product page for live product details relevant to your question.")

        response = {
            "conversation_id": conversation_id,
            "mode": "product_followup",
            "answer": "\n".join(lines),
            "results": [result],
        }
        if include_tool_trace:
            response["tool_calls"] = trace
        return response

    def _handle_compare_followup(
        self,
        *,
        query: str,
        comparison_offers: list[dict[str, Any]],
        conversation_id: str,
        user_id: str | None,
        include_tool_trace: bool,
    ) -> dict[str, Any]:
        if len(comparison_offers) < 2:
            return self._clarification_response(
                conversation_id=conversation_id,
                question="Which two products do you want me to compare? Say 'compare product 1 and 3' or name them.",
            )

        left_response = self._handle_product_followup(
            query="details",
            reference_offer=comparison_offers[0],
            conversation_id=conversation_id,
            user_id=user_id,
            include_tool_trace=True,
        )
        right_response = self._handle_product_followup(
            query="details",
            reference_offer=comparison_offers[1],
            conversation_id=conversation_id,
            user_id=user_id,
            include_tool_trace=True,
        )

        left = dict((left_response.get("results") or [{}])[0])
        right = dict((right_response.get("results") or [{}])[0])
        trace = list(left_response.get("tool_calls", [])) + list(right_response.get("tool_calls", []))

        def _name(row: dict[str, Any]) -> str:
            return str(row.get("title") or "product").strip()

        def _source(row: dict[str, Any]) -> str:
            return str(row.get("source") or "store").strip()

        def _rating_text(row: dict[str, Any]) -> str:
            value = row.get("rating")
            try:
                return "N/A" if value is None else f"{float(value):.1f}"
            except (TypeError, ValueError):
                return str(value)

        def _review_count_text(row: dict[str, Any]) -> str:
            value = _safe_int(row.get("review_count"))
            return "N/A" if value is None else str(value)

        focus = _response_focus(query)
        lines = [f"Comparison between {_name(left)} and {_name(right)}:"]

        if focus == "delivery":
            for row in (left, right):
                shipping = _safe_float(row.get("shipping_price_pkr"))
                if shipping is None:
                    shipping = _safe_float(row.get("shipping_pkr"))
                if shipping is None:
                    shipping_txt = "no explicit delivery charge found"
                elif shipping <= 0:
                    shipping_txt = "free delivery"
                else:
                    shipping_txt = f"delivery PKR {_fmt_price(shipping)}"
                delivery_info = str(row.get("delivery_info") or row.get("shipping_summary") or "").strip()
                detail = f"{_name(row)} ({_source(row)}): {shipping_txt}"
                if delivery_info:
                    detail += f"; {delivery_info}"
                lines.append(_punctuate_line(detail))
        elif focus == "reviews":
            for row in (left, right):
                lines.append(
                    _punctuate_line(
                        f"{_name(row)} ({_source(row)}): rating {_rating_text(row)}, reviews {row.get('review_count') or 'N/A'}"
                    )
                )
        elif focus == "price":
            for row in (left, right):
                lines.append(
                    _punctuate_line(
                        f"{_name(row)} ({_source(row)}): PKR {_fmt_price(row.get('total_price_pkr') or row.get('price_pkr'))}"
                    )
                )
        elif focus == "specs":
            for row in (left, right):
                specs = str(row.get("specifications") or "").strip()
                if len(specs) > 180:
                    specs = specs[:177].rstrip() + "..."
                lines.append(
                    _punctuate_line(
                        f"{_name(row)} ({_source(row)}): "
                        + (f"Specs: {specs}" if specs else "No clear specs were found in the current pass")
                    )
                )
        else:
            for row in (left, right):
                availability = row.get("availability")
                if not availability and row.get("in_stock") is not None:
                    availability = "in stock" if row.get("in_stock") else "out of stock"
                detail = (
                    f"{_name(row)} ({_source(row)}): PKR {_fmt_price(row.get('total_price_pkr') or row.get('price_pkr'))}, "
                    f"rating {_rating_text(row)}, reviews {row.get('review_count') or 'N/A'}"
                )
                if availability:
                    detail += f", {availability}"
                lines.append(_punctuate_line(detail))

            left_price = _safe_float(left.get("total_price_pkr") or left.get("price_pkr"))
            right_price = _safe_float(right.get("total_price_pkr") or right.get("price_pkr"))
            left_rating = _safe_float(left.get("rating"))
            right_rating = _safe_float(right.get("rating"))
            if left_price is not None and right_price is not None and left_rating is not None and right_rating is not None:
                if left_price <= right_price and left_rating >= right_rating:
                    lines.append(f"Verdict: {_name(left)} looks like the stronger value pick on current price and rating.")
                elif right_price <= left_price and right_rating >= left_rating:
                    lines.append(f"Verdict: {_name(right)} looks like the stronger value pick on current price and rating.")
                elif left_rating > right_rating and left_price <= right_price * 1.15:
                    lines.append(f"Verdict: {_name(left)} looks stronger overall because its rating is better without a major price penalty.")
                elif right_rating > left_rating and right_price <= left_price * 1.15:
                    lines.append(f"Verdict: {_name(right)} looks stronger overall because its rating is better without a major price penalty.")

        left_price = _safe_float(left.get("total_price_pkr") or left.get("price_pkr"))
        right_price = _safe_float(right.get("total_price_pkr") or right.get("price_pkr"))
        left_rating = _safe_float(left.get("rating"))
        right_rating = _safe_float(right.get("rating"))
        left_reviews = _safe_int(left.get("review_count")) or 0
        right_reviews = _safe_int(right.get("review_count")) or 0
        qn = normalize_text(query)

        if left_price is not None and right_price is not None:
            if left_price < right_price:
                cheaper, pricier = left, right
                cheaper_price, pricier_price = left_price, right_price
                cheaper_rating, pricier_rating = left_rating, right_rating
                cheaper_reviews, pricier_reviews = left_reviews, right_reviews
            else:
                cheaper, pricier = right, left
                cheaper_price, pricier_price = right_price, left_price
                cheaper_rating, pricier_rating = right_rating, left_rating
                cheaper_reviews, pricier_reviews = right_reviews, left_reviews

            diff = pricier_price - cheaper_price
            lines.append(
                f"Price gap: PKR {_fmt_price(diff)} ({_name(cheaper)} is cheaper than {_name(pricier)})."
            )
            if any(marker in qn for marker in ("worth", "higher price", "paying more", "extra")):
                rating_gap = None
                if cheaper_rating is not None and pricier_rating is not None:
                    rating_gap = pricier_rating - cheaper_rating
                reviews_gain = pricier_reviews - cheaper_reviews
                if rating_gap is not None and rating_gap >= 0.4 and diff <= cheaper_price * 0.2:
                    lines.append(
                        f"Verdict: {_name(pricier)} looks worth the extra PKR {_fmt_price(diff)} because its rating is meaningfully stronger."
                    )
                elif rating_gap is not None and rating_gap <= 0.1 and diff >= max(500.0, cheaper_price * 0.15):
                    lines.append(
                        f"Verdict: {_name(pricier)} does not look worth the extra PKR {_fmt_price(diff)} on current price and rating alone."
                    )
                elif reviews_gain >= 50 and diff <= cheaper_price * 0.15:
                    lines.append(
                        f"Verdict: {_name(pricier)} may be worth the extra PKR {_fmt_price(diff)} because it has much stronger buyer signal volume."
                    )
                else:
                    lines.append(
                        f"Verdict: {_name(cheaper)} is the safer value pick unless you specifically want what makes {_name(pricier)} more premium."
                    )
            elif any(marker in qn for marker in ("which is better", "which one is better", "better option", "better buy", "better value")):
                left_score = (left_rating or 0.0) * 0.55 + min(1.0, math.log1p(left_reviews) / 6.0) * 0.15 + (
                    0.30 / (1.0 + (left_price / 250000.0))
                    if left_price is not None
                    else 0.0
                )
                right_score = (right_rating or 0.0) * 0.55 + min(1.0, math.log1p(right_reviews) / 6.0) * 0.15 + (
                    0.30 / (1.0 + (right_price / 250000.0))
                    if right_price is not None
                    else 0.0
                )
                if left_score > right_score:
                    lines.append(f"Verdict: {_name(left)} looks like the better overall buy from the current price, rating, and review signals.")
                elif right_score > left_score:
                    lines.append(f"Verdict: {_name(right)} looks like the better overall buy from the current price, rating, and review signals.")

        if focus == "reviews":
            stronger = None
            if left_rating is not None and right_rating is not None:
                if left_rating > right_rating:
                    stronger = left
                elif right_rating > left_rating:
                    stronger = right
            if stronger is not None:
                lines.append(
                    f"For review confidence, {_name(stronger)} looks stronger right now ({_rating_text(stronger)} rating, {_review_count_text(stronger)} reviews)."
                )

        response = {
            "conversation_id": conversation_id,
            "mode": "compare_products",
            "answer": "\n".join(lines),
            "results": [left, right],
        }
        if include_tool_trace:
            response["tool_calls"] = trace
        return response

    def _handle_refine_results(self, *, query: str, state: dict[str, Any], conversation_id: str) -> dict[str, Any]:
        raw_last_search_results = state.get("last_search_results")
        if isinstance(raw_last_search_results, list):
            base_rows: list[dict[str, Any]] = [row for row in raw_last_search_results if isinstance(row, dict)]
        else:
            base_rows = self._state_results(state)
        rows = [dict(row) for row in base_rows if isinstance(row, dict)]
        if not rows:
            return {
                "conversation_id": conversation_id,
                "mode": "refine_previous_results",
                "answer": "I do not have a previous result set to refine yet. Start with a search first.",
                "results": [],
            }

        query_normalized = normalize_text(query)
        filtered = list(rows)
        notes: list[str] = []

        active_offer = state.get("active_offer") or state.get("last_reference_offer")
        active_price = _safe_float((active_offer or {}).get("total_price_pkr") or (active_offer or {}).get("price_pkr"))
        active_brand = normalize_text(str((active_offer or {}).get("brand") or (active_offer or {}).get("source") or ""))

        if any(marker in query_normalized for marker in ("cheaper", "cheapest", "more affordable", "budget")) and active_price is not None:
            filtered = [
                row
                for row in filtered
                if str(row.get("link", "")).strip() != str((active_offer or {}).get("link", "")).strip()
                and ((row_price := _safe_float(row.get("total_price_pkr") or row.get("price_pkr"))) is not None)
                and row_price < active_price
            ]
            notes.append(f"cheaper than the selected product (PKR {_fmt_price(active_price)})")

        max_price = _extract_max_price(query)
        if max_price is not None:
            filtered = [
                row
                for row in filtered
                if ((row_price := _safe_float(row.get("total_price_pkr") or row.get("price_pkr"))) is not None)
                and row_price <= max_price
            ]
            notes.append(f"price <= PKR {_fmt_price(max_price)}")

        min_price = _extract_min_price(query)
        if min_price is not None:
            filtered = [
                row
                for row in filtered
                if ((row_price := _safe_float(row.get("total_price_pkr") or row.get("price_pkr"))) is not None)
                and row_price >= min_price
            ]
            notes.append(f"price >= PKR {_fmt_price(min_price)}")

        min_rating = _extract_min_rating(query)
        if min_rating is not None:
            filtered = [
                row
                for row in filtered
                if ((row_rating := _safe_float(row.get("rating"))) is not None) and row_rating >= min_rating
            ]
            notes.append(f"rating >= {min_rating:.1f}")

        source_filters = _extract_source_filters(query)
        if source_filters:
            filtered = [
                row
                for row in filtered
                if any(source in normalize_text(str(row.get("source", ""))) for source in source_filters)
            ]
            notes.append("source in " + ", ".join(source_filters))

        if active_brand and any(marker in query_normalized for marker in ("same brand", "same store", "same source")):
            filtered = [
                row
                for row in filtered
                if active_brand
                in normalize_text(
                    " ".join(
                        [
                            str(row.get("brand") or ""),
                            str(row.get("source") or ""),
                        ]
                    )
                )
            ]
            notes.append(f"same brand/source as the current product ({active_brand})")

        token_filters = _extract_refine_tokens(query)
        if token_filters:
            narrowed = []
            for row in filtered:
                haystack = normalize_text(
                    " ".join(
                        str(row.get(field, "") or "")
                        for field in ("title", "source", "specifications", "brand", "model", "category", "subcategory")
                    )
                )
                if token_filters.intersection(set(haystack.split())):
                    narrowed.append(row)
            if narrowed:
                filtered = narrowed
                notes.append("matching tokens: " + ", ".join(sorted(token_filters)))

        if any(marker in query_normalized for marker in ("highest rating", "best rated", "top rated", "better reviewed")) or (
            "sort" in query_normalized and "rating" in query_normalized
        ):
            filtered.sort(
                key=lambda row: (
                    _safe_float(row.get("rating")) if _safe_float(row.get("rating")) is not None else -1.0,
                    _safe_int(row.get("review_count")) if _safe_int(row.get("review_count")) is not None else -1,
                ),
                reverse=True,
            )
            notes.append("sorted by rating")
        elif any(marker in query_normalized for marker in ("most reviewed", "best reviews", "highest reviews")) or (
            "sort" in query_normalized and "review" in query_normalized
        ):
            filtered.sort(
                key=lambda row: (
                    _safe_int(row.get("review_count")) if _safe_int(row.get("review_count")) is not None else -1,
                    _safe_float(row.get("rating")) if _safe_float(row.get("rating")) is not None else -1.0,
                ),
                reverse=True,
            )
            notes.append("sorted by reviews")
        elif (
            "cheaper" in query_normalized
            or "cheapest" in query_normalized
            or "more affordable" in query_normalized
            or "lowest price" in query_normalized
            or ("sort" in query_normalized and "price" in query_normalized)
        ):
            filtered.sort(key=_price_for_sort)
            notes.append("sorted by price")

        if not filtered:
            detail = ", ".join(notes) if notes else "your filters"
            answer = f"None of the current results matched {detail}. You may need a fresh search with broader constraints."
            return {
                "conversation_id": conversation_id,
                "mode": "refine_previous_results",
                "answer": answer,
                "results": [],
            }

        if notes:
            intro = "I refined the previous results using: " + ", ".join(notes) + "."
        else:
            intro = "I refined the previous results."
        answer = intro + "\n" + self._build_grounded_answer(query=query, results=filtered)
        return {
            "conversation_id": conversation_id,
            "mode": "refine_previous_results",
            "answer": answer,
            "results": filtered,
        }

    def _handle_review_followup(
        self,
        *,
        query: str,
        reference_offer: dict[str, Any],
        conversation_id: str,
        user_id: str | None,
        top_k: int,
        include_tool_trace: bool,
    ) -> dict[str, Any]:
        trace: list[dict[str, Any]] = []
        context_payload = {"conversation_id": conversation_id, "user_id": user_id}
        inspect_args = {"link": reference_offer.get("link")}
        if reference_offer.get("source"):
            inspect_args["source"] = reference_offer.get("source")
        if reference_offer.get("title"):
            inspect_args["title_hint"] = reference_offer.get("title")
        try:
            inspect_out = self.tools.call_tool("inspect_product_page", inspect_args, context=context_payload)
        except Exception as exc:
            inspect_out = {"ok": False, "tool_name": "inspect_product_page", "error": str(exc), "result": {}}
        trace.append({"tool_name": "inspect_product_page", "arguments": inspect_args, "output": _to_jsonable(inspect_out)})

        inspected = (inspect_out.get("result") or {}) if inspect_out.get("ok") else {}
        inspected_is_listing = str(inspected.get("page_type") or "").strip().lower() == "listing"
        has_signals = bool(inspected.get("has_review_signals")) and not inspected_is_listing
        results = [
            {
                "title": inspected.get("title") or reference_offer.get("title"),
                "link": inspected.get("final_url") or inspected.get("link") or reference_offer.get("link"),
                "source": inspected.get("source") or reference_offer.get("source"),
                "image": inspected.get("image") or reference_offer.get("image"),
                "price_pkr": inspected.get("price_pkr"),
                "total_price_pkr": inspected.get("price_pkr"),
                "rating": inspected.get("rating"),
                "review_count": inspected.get("review_count"),
                "match_score": None,
                "rank_score": None,
                "reason": "Direct page inspection for rating/review signals.",
            }
        ]

        answer: str
        if has_signals:
            title = str(inspected.get("title") or reference_offer.get("title") or "selected product")
            source = str(inspected.get("source") or reference_offer.get("source") or "website")
            rating = inspected.get("rating")
            review_count = inspected.get("review_count")
            rating_txt = "N/A" if rating is None else f"{float(rating):.1f}"
            review_txt = "N/A" if review_count in (None, "") else str(review_count)
            price_txt = _fmt_price(inspected.get("price_pkr"))
            snippets = inspected.get("review_snippets") or []
            snippet_txt = ""
            if snippets:
                snippet_txt = f"\nSample review: {str(snippets[0])[:220]}"
            answer = (
                f"Review check for {title} ({source}): rating {rating_txt}, reviews {review_txt}, "
                f"price PKR {price_txt}.{snippet_txt}"
            )
        else:
            title = str(reference_offer.get("title", "selected product"))
            review_query = f"{title} reviews rating pakistan"
            try:
                review_out = self.tools.call_tool(
                    "search_web_products",
                    {"query": review_query, "top_k": min(max(3, top_k), 8)},
                    context=context_payload,
                )
            except Exception as exc:
                review_out = {"ok": False, "tool_name": "search_web_products", "error": str(exc), "result": {}}
            trace.append({"tool_name": "search_web_products", "arguments": {"query": review_query}, "output": _to_jsonable(review_out)})
            live_rows = (
                filter_relevant_results(
                    review_query,
                    _normalize_live_offers(list((review_out.get("result") or {}).get("offers") or [])),
                )
                if review_out.get("ok")
                else []
            )
            if live_rows:
                results = live_rows
                best = live_rows[0]
                best_rating = best.get("rating")
                best_rating_txt = "N/A" if best_rating is None else f"{float(best_rating):.1f}"
                answer = (
                    f"Direct page review signals were limited for {title}. "
                    f"I searched live web review/offers and found {len(live_rows)} related listings. "
                    f"Top match: {best.get('title')} ({best.get('source')}) "
                    f"rating {best_rating_txt}."
                )
            else:
                detail_msg = (
                    "Inspection landed on a category/listing page, so I did not trust those page-level review signals."
                    if inspected_is_listing
                    else "I checked the selected product page for review signals but couldn't find structured ratings/reviews."
                )
                answer = detail_msg + " No stronger live review sources were found in this pass."

        response = {
            "conversation_id": conversation_id,
            "mode": "review_followup",
            "answer": answer,
            "results": results,
        }
        if include_tool_trace:
            response["tool_calls"] = trace
        return response

    def _build_grounded_answer(self, *, query: str, results: list[dict[str, Any]], fallback_text: str | None = None) -> str:
        if not results:
            return fallback_text or "No matching offers were found."

        qn = normalize_text(query)
        rows = list(results)
        sorted_by_price = ("sort" in qn and "price" in qn) or ("lowest" in qn and "price" in qn)
        if sorted_by_price:
            rows.sort(key=_price_for_sort)

        lines: list[str] = []
        heading = "Offers sorted by price (lowest to highest):" if sorted_by_price else "Top matching offers:"
        lines.append(heading)
        for idx, row in enumerate(rows[:5], start=1):
            title = str(row.get("title", "Untitled")).strip()
            source = str(row.get("source", "unknown")).strip()
            price = _fmt_price(row.get("total_price_pkr") or row.get("price_pkr"))
            display_rating, display_rating_kind = _display_rating_value(row)
            rating_txt = "N/A" if display_rating is None else f"{display_rating:.1f}"
            range_txt = _format_price_range(row)
            link = str(row.get("link", "")).strip()
            price_bits = [f"PKR {price}"]
            if range_txt:
                price_bits.append(f"range {range_txt}")
            rating_bits = f"rating {rating_txt}"
            if display_rating_kind == "predicted":
                rating_bits += " (pred.)"
            if link:
                lines.append(f"{idx}. {title} | {source} | {' | '.join(price_bits)} | {rating_bits} | {link}")
            else:
                lines.append(f"{idx}. {title} | {source} | {' | '.join(price_bits)} | {rating_bits}")

        return "\n".join(lines)
