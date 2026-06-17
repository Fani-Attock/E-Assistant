from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import re
import socket
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from prometheus_client import Counter, Histogram
from pymongo import MongoClient
import requests
from bs4 import BeautifulSoup

from src.core.interaction_store import EVENT_WEIGHTS, log_interaction
from src.core.logging_utils import setup_logging
from src.core.marketplace import (
    apply_display_source_fields,
    get_marketplace_projection,
    product_family_key,
    seller_product_to_search_doc,
)
from src.core.marketplace_analytics import derive_prediction_defaults, summarize_product_report
from src.core.normalize import normalize_image_gallery, normalize_search_query, normalize_text, offer_id_from_source_link
from src.core.settings import Settings
from src.core.search_pipeline import SearchPipeline

logger = setup_logging("mcp.server")

TOOL_CALLS_TOTAL = Counter(
    "assistant_tool_calls_total",
    "Total assistant tool calls by tool and status.",
    ["tool_name", "status"],
)
TOOL_CALL_FAILURES_TOTAL = Counter(
    "assistant_tool_failures_total",
    "Assistant tool failures by tool, error type, and domain.",
    ["tool_name", "error_type", "domain"],
)
TOOL_CALL_LATENCY_SECONDS = Histogram(
    "assistant_tool_latency_seconds",
    "Assistant tool call latency in seconds.",
    ["tool_name", "status"],
)

ToolHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


class MCPToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


def _validate_type(name: str, value: Any, expected: str) -> None:
    if expected == "string" and not isinstance(value, str):
        raise MCPToolError(f"Invalid type for '{name}': expected string.")
    if expected == "number" and not isinstance(value, (int, float)):
        raise MCPToolError(f"Invalid type for '{name}': expected number.")
    if expected == "integer" and not isinstance(value, int):
        raise MCPToolError(f"Invalid type for '{name}': expected integer.")
    if expected == "boolean" and not isinstance(value, bool):
        raise MCPToolError(f"Invalid type for '{name}': expected boolean.")
    if expected == "object" and not isinstance(value, dict):
        raise MCPToolError(f"Invalid type for '{name}': expected object.")
    if expected == "array" and not isinstance(value, list):
        raise MCPToolError(f"Invalid type for '{name}': expected array.")


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _maybe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, list):
        return [_iso(x) for x in value]
    if isinstance(value, dict):
        return {k: _iso(v) for k, v in value.items()}
    return value


def _extract_json_payload(text: str) -> dict[str, Any]:
    content = (text or "").strip()
    if content.startswith("{") and content.endswith("}"):
        return json.loads(content)
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(content[start : end + 1])
    raise ValueError("No JSON object in model response")


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _extract_from_jsonld_payload(payload: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            typ = obj.get("@type")
            if isinstance(typ, str) and typ.lower() == "product":
                nodes.append(obj)
            elif isinstance(typ, list) and any(str(x).lower() == "product" for x in typ):
                nodes.append(obj)
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(payload)
    return nodes


def _extract_jsonld_products(soup: BeautifulSoup) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        out.extend(_extract_from_jsonld_payload(payload))
    return out


def _extract_price_candidates_from_text(text: str) -> list[float]:
    candidates: list[float] = []
    patterns = [
        r"(?:pkr|rs\.?|rupees)\s*([0-9][0-9,]*(?:\.\d+)?)",
        r"([0-9][0-9,]*(?:\.\d+)?)\s*(?:pkr|rs\.?|rupees)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            raw = match.group(1).replace(",", "")
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if 50 <= value <= 5_000_000:
                candidates.append(value)
    return candidates


def _clean_text_snippet(value: str, limit: int = 220) -> str:
    text = " ".join(str(value or "").split()).strip(" -:|")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _extract_context_snippets(
    text: str,
    keywords: tuple[str, ...],
    *,
    max_snippets: int = 3,
    radius_before: int = 80,
    radius_after: int = 120,
) -> list[str]:
    normalized = " ".join((text or "").split())
    if not normalized or not keywords:
        return []
    pattern = re.compile("|".join(re.escape(keyword) for keyword in keywords), flags=re.IGNORECASE)
    snippets: list[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(normalized):
        start = max(0, match.start() - radius_before)
        end = min(len(normalized), match.end() + radius_after)
        snippet = _clean_text_snippet(normalized[start:end])
        if not snippet:
            continue
        key = snippet.lower()
        if key in seen:
            continue
        seen.add(key)
        snippets.append(snippet)
        if len(snippets) >= max_snippets:
            break
    return snippets


def _extract_shipping_signals(text: str) -> tuple[float | None, str | None, str | None]:
    normalized = " ".join((text or "").split())
    if not normalized:
        return None, None, None

    shipping_price: float | None = None
    if re.search(r"\bfree\s+(?:delivery|shipping)\b", normalized, flags=re.IGNORECASE):
        shipping_price = 0.0

    patterns = [
        r"(?:delivery|shipping|courier)[^.!?\n\r]{0,60}?(?:charges?|charge|fee|fees|cost)?[^.!?\n\r]{0,20}?(?:pkr|rs\.?|rupees)\s*([0-9][0-9,]*(?:\.\d+)?)",
        r"(?:pkr|rs\.?|rupees)\s*([0-9][0-9,]*(?:\.\d+)?)[^.!?\n\r]{0,40}?(?:delivery|shipping|courier)(?:\s+charges?|\s+charge|\s+fee|\s+fees|\s+cost)?",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            raw = match.group(1).replace(",", "")
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if 0 <= value <= 100_000:
                shipping_price = value
                break
        if shipping_price is not None and shipping_price > 0:
            break

    snippets = _extract_context_snippets(
        normalized,
        ("delivery", "shipping", "courier", "dispatch", "delivers", "shipment"),
        max_snippets=3,
    )
    shipping_summary = snippets[0] if snippets else None
    delivery_info = snippets[1] if len(snippets) > 1 else shipping_summary
    return shipping_price, shipping_summary, delivery_info


def _extract_warranty_info(text: str) -> str | None:
    snippets = _extract_context_snippets(text, ("warranty", "guarantee"), max_snippets=1, radius_before=60, radius_after=140)
    return snippets[0] if snippets else None


def _title_token_set(value: str | None) -> set[str]:
    return {token for token in normalize_text(value or "").split() if len(token) >= 3}


def _title_overlap_ratio(left: str | None, right: str | None) -> float:
    left_tokens = _title_token_set(left)
    right_tokens = _title_token_set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens.intersection(right_tokens)) / max(1, min(len(left_tokens), len(right_tokens)))


def _classify_page_type(
    *,
    final_url: str,
    page_title: str | None,
    title_hint: str | None,
    jsonld_product_count: int,
    text: str,
) -> tuple[str, str | None]:
    path = (urlparse(final_url).path or "").lower()
    title_low = (page_title or "").strip().lower()
    text_low = (text or "").lower()
    title_overlap = _title_overlap_ratio(page_title, title_hint)

    listing_path_markers = (
        "/collections/",
        "/collection/",
        "/category/",
        "/categories/",
        "/search",
        "/pricelist",
        "/price-list",
    )
    listing_title_markers = (
        "price list",
        "prices in pakistan",
        "shop all",
        "all products",
        "best ",
        " top ",
        "collection",
        "category",
    )

    listing_score = 0
    product_score = 0

    if jsonld_product_count > 1:
        listing_score += 3
    elif jsonld_product_count == 1:
        product_score += 3

    if any(marker in path for marker in listing_path_markers):
        listing_score += 2
    if any(marker in title_low for marker in listing_title_markers):
        listing_score += 2
    if title_overlap >= 0.5:
        product_score += 2
    elif title_overlap >= 0.25:
        product_score += 1

    if re.search(r"\b(add to cart|buy now|sku|quantity|installments?)\b", text_low):
        product_score += 1
    if re.search(r"\b(sort by|filter by|showing \d+|collections?|categories|price list|shop now)\b", text_low):
        listing_score += 1

    if listing_score >= product_score + 2:
        return "listing", "Inspection landed on a category/listing page; live page details may be generic."
    if product_score >= listing_score + 1:
        return "product", None
    return "unknown", None


def _is_private_or_special_ip(ip_text: str) -> bool:
    ip = ipaddress.ip_address(ip_text)
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _root_domain(host: str) -> str:
    normalized = (host or "").strip().lower()
    if not normalized:
        return "unknown"
    normalized = normalized.split("@")[-1].split(":")[0]
    labels = [x for x in normalized.split(".") if x]
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return normalized


def _extract_domain_from_arguments(arguments: dict[str, Any]) -> str:
    for key in ("link", "url"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            host = urlparse(value).netloc
            if host:
                return _root_domain(host)
    source = arguments.get("source")
    if isinstance(source, str) and source.strip():
        return _root_domain(source.strip())
    return "unknown"


def _classify_error(exc: Exception) -> str:
    name = exc.__class__.__name__.strip() or "error"
    return name.lower()


class MCPToolServer:
    def __init__(self, settings: Settings, pipeline: SearchPipeline | None = None, db=None):
        self.settings = settings
        self.pipeline = pipeline or SearchPipeline(settings)
        if db is None:
            client = MongoClient(settings.mongo_uri)
            db = client[settings.app_db_name]
        self.db = db
        self._tools: dict[str, tuple[ToolDefinition, ToolHandler]] = {}
        self._register_tools()

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool, _ in self._tools.values()
        ]

    def call_tool(self, name: str, arguments: dict[str, Any] | None, context: dict[str, Any] | None = None) -> dict[str, Any]:
        if name not in self._tools:
            raise MCPToolError(f"Unknown tool: {name}")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise MCPToolError("Tool arguments must be a JSON object")
        context = context or {}
        tool, handler = self._tools[name]
        self._validate_tool_arguments(tool, arguments)
        started = datetime.now(timezone.utc)
        domain = _extract_domain_from_arguments(arguments)
        try:
            result = handler(arguments, context)
            elapsed = max(0.0, (datetime.now(timezone.utc) - started).total_seconds())
            TOOL_CALLS_TOTAL.labels(tool_name=name, status="ok").inc()
            TOOL_CALL_LATENCY_SECONDS.labels(tool_name=name, status="ok").observe(elapsed)
            output = {
                "ok": True,
                "tool_name": name,
                "result": _iso(result),
            }
            self._log_tool_call(
                conversation_id=context.get("conversation_id"),
                user_id=context.get("user_id"),
                tool_name=name,
                arguments=arguments,
                result=output["result"],
                started_at=started,
                error=None,
                error_type=None,
                domain=domain,
            )
            return output
        except Exception as exc:
            elapsed = max(0.0, (datetime.now(timezone.utc) - started).total_seconds())
            error_type = _classify_error(exc)
            TOOL_CALLS_TOTAL.labels(tool_name=name, status="error").inc()
            TOOL_CALL_FAILURES_TOTAL.labels(tool_name=name, error_type=error_type, domain=domain).inc()
            TOOL_CALL_LATENCY_SECONDS.labels(tool_name=name, status="error").observe(elapsed)
            self._log_tool_call(
                conversation_id=context.get("conversation_id"),
                user_id=context.get("user_id"),
                tool_name=name,
                arguments=arguments,
                result=None,
                started_at=started,
                error=str(exc),
                error_type=error_type,
                domain=domain,
            )
            raise

    def _prediction_for_product(self, product_id: str) -> dict[str, Any] | None:
        collection_name = getattr(self.settings, "marketplace_predictions_collection", "")
        if not product_id or not collection_name:
            return None
        return self.db[collection_name].find_one({"product_id": product_id}, {"_id": 0})

    def _sales_report_for_offer(self, offer: dict[str, Any]) -> dict[str, Any]:
        product_id = str(offer.get("product_id") or "")
        offer_id = str(offer.get("offer_id") or "")
        orders_collection = getattr(self.settings, "marketplace_orders_collection", "")
        interactions_collection = getattr(self.settings, "interactions_collection", "")
        orders = list(self.db[orders_collection].find({"product_id": product_id}, {"_id": 0})) if orders_collection else []
        interactions = (
            list(self.db[interactions_collection].find({"offer_id": offer_id}, {"_id": 0, "offer_id": 1, "event_type": 1, "event_ts": 1}))
            if interactions_collection
            else []
        )
        return summarize_product_report(
            product=offer,
            orders=orders,
            interactions=interactions,
            prediction=self._prediction_for_product(product_id),
        )

    def _price_range_for_offer(self, offer: dict[str, Any]) -> dict[str, Any]:
        family_key = product_family_key(offer)
        prices: list[float] = []
        normalized_collection = self.db[self.settings.normalized_collection]
        if hasattr(normalized_collection, "find"):
            normalized_rows = list(
                normalized_collection.find(
                    {"in_stock": True},
                    {"_id": 0, "title": 1, "brand": 1, "model": 1, "category": 1, "price_pkr": 1, "shipping_pkr": 1},
                ).limit(4000)
            )
        else:
            normalized_rows = []
        for row in normalized_rows:
            row = dict(row)
            row["total_price_pkr"] = (
                float(row.get("price_pkr")) + float(row.get("shipping_pkr") or 0.0)
                if row.get("price_pkr") not in (None, "")
                else None
            )
            if product_family_key(row) != family_key:
                continue
            price = row.get("total_price_pkr") if row.get("total_price_pkr") is not None else row.get("price_pkr")
            try:
                if price is not None:
                    prices.append(float(price))
            except (TypeError, ValueError):
                continue
        seller_collection = self.db[self.settings.marketplace_seller_products_collection]
        if hasattr(seller_collection, "find"):
            seller_rows = list(
                seller_collection.find(
                    {"status": "active"},
                    get_marketplace_projection(),
                ).limit(4000)
            )
        else:
            seller_rows = []
        for row in seller_rows:
            offer_row = seller_product_to_search_doc(row)
            offer_row["total_price_pkr"] = (
                float(offer_row.get("price_pkr")) + float(offer_row.get("shipping_pkr") or 0.0)
                if offer_row.get("price_pkr") not in (None, "")
                else None
            )
            if product_family_key(offer_row) != family_key:
                continue
            price = offer_row.get("total_price_pkr") if offer_row.get("total_price_pkr") is not None else offer_row.get("price_pkr")
            try:
                if price is not None:
                    prices.append(float(price))
            except (TypeError, ValueError):
                continue
        if not prices:
            return offer
        enriched = dict(offer)
        enriched["price_range_pkr_min"] = round(min(prices), 2)
        enriched["price_range_pkr_max"] = round(max(prices), 2)
        return enriched

    def _validate_tool_arguments(self, tool: ToolDefinition, arguments: dict[str, Any]) -> None:
        schema = tool.input_schema or {}
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        allow_unknown = bool(schema.get("additionalProperties", False))

        for key in required:
            if key not in arguments or arguments.get(key) in (None, ""):
                raise MCPToolError(f"Missing required argument '{key}' for tool '{tool.name}'.")

        for key, value in arguments.items():
            if key not in props:
                if allow_unknown:
                    continue
                raise MCPToolError(f"Unknown argument '{key}' for tool '{tool.name}'.")
            rule = props.get(key) or {}
            expected = rule.get("type")
            if expected:
                _validate_type(key, value, expected)
            if isinstance(value, str):
                min_len = rule.get("minLength")
                max_len = rule.get("maxLength")
                if isinstance(min_len, int) and len(value) < min_len:
                    raise MCPToolError(f"Argument '{key}' is shorter than minLength={min_len}.")
                if isinstance(max_len, int) and len(value) > max_len:
                    raise MCPToolError(f"Argument '{key}' exceeds maxLength={max_len}.")
            if isinstance(value, (int, float)):
                min_v = rule.get("minimum")
                max_v = rule.get("maximum")
                if isinstance(min_v, (int, float)) and value < min_v:
                    raise MCPToolError(f"Argument '{key}' is below minimum={min_v}.")
                if isinstance(max_v, (int, float)) and value > max_v:
                    raise MCPToolError(f"Argument '{key}' exceeds maximum={max_v}.")
            enum = rule.get("enum")
            if isinstance(enum, list) and value not in enum:
                raise MCPToolError(f"Argument '{key}' has unsupported value '{value}'.")

    def _validate_safe_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise MCPToolError("Only http/https URLs are allowed.")
        host = (parsed.hostname or "").strip().lower()
        if not host:
            raise MCPToolError("URL hostname is missing.")
        if host in {"localhost"} or host.endswith(".local"):
            raise MCPToolError("Localhost/local domains are blocked.")
        if not self.settings.inspect_page_block_private_networks:
            return
        try:
            addr_info = socket.getaddrinfo(host, None)
        except Exception as exc:
            raise MCPToolError(f"dns_resolution_failed:{exc}") from exc
        ips: set[str] = set()
        for row in addr_info:
            sockaddr = row[4]
            if not sockaddr:
                continue
            ip_text = sockaddr[0]
            if ip_text:
                ips.add(ip_text)
        if not ips:
            raise MCPToolError("No resolved IPs for target URL.")
        for ip_text in ips:
            try:
                if _is_private_or_special_ip(ip_text):
                    raise MCPToolError(f"Blocked private/special IP target: {ip_text}")
            except ValueError:
                continue

    def _fetch_html_safe(self, url: str, headers: dict[str, str]) -> tuple[str, str]:
        if not self.settings.inspect_page_enabled:
            raise MCPToolError("Page inspection is disabled by configuration.")
        self._validate_safe_url(url)
        timeout = max(3, int(self.settings.inspect_page_timeout_sec))
        max_redirects = max(0, int(self.settings.inspect_page_max_redirects))
        max_bytes = max(100_000, int(self.settings.inspect_page_max_response_bytes))

        try:
            response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True, stream=True)
            response.raise_for_status()
        except Exception as exc:
            raise MCPToolError(f"page_fetch_failed:{exc}") from exc

        try:
            if len(response.history) > max_redirects:
                raise MCPToolError(f"Too many redirects: {len(response.history)} > {max_redirects}")

            for hop in list(response.history) + [response]:
                hop_url = str(hop.url)
                self._validate_safe_url(hop_url)

            content_type = str(response.headers.get("content-type", "")).lower()
            if content_type and all(x not in content_type for x in ("text", "html", "xml", "json")):
                raise MCPToolError(f"Unsupported content-type for page inspection: {content_type}")

            content = bytearray()
            for chunk in response.iter_content(chunk_size=16384):
                if not chunk:
                    continue
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise MCPToolError(f"Response too large for inspection (> {max_bytes} bytes)")
            encoding = response.encoding or "utf-8"
            html = bytes(content).decode(encoding, errors="ignore")
            return str(response.url), html
        finally:
            response.close()

    def _register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        self._tools[definition.name] = (definition, handler)

    def _register_tools(self) -> None:
        self._register(
            ToolDefinition(
                name="search_offers",
                description="Search product offers across stores and return ranked best-value results.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 2},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                        "user_id": {"type": "string"},
                        "min_rating": {"type": "number", "minimum": 0, "maximum": 5},
                    },
                    "required": ["query"],
                },
            ),
            self._tool_search_offers,
        )
        self._register(
            ToolDefinition(
                name="search_web_products",
                description="Run live web product search and return current market offers from indexed web sources.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 2},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                        "country": {"type": "string"},
                    },
                    "required": ["query"],
                },
            ),
            self._tool_search_web_products,
        )
        self._register(
            ToolDefinition(
                name="inspect_product_page",
                description="Inspect a specific product page URL and extract rating/review/price signals from page content.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "link": {"type": "string"},
                        "source": {"type": "string"},
                        "title_hint": {"type": "string"},
                    },
                    "required": ["link"],
                },
            ),
            self._tool_inspect_product_page,
        )
        self._register(
            ToolDefinition(
                name="get_offer_details",
                description="Fetch full normalized record for a single offer by offer_id or source+link.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "offer_id": {"type": "string"},
                        "source": {"type": "string"},
                        "link": {"type": "string"},
                    },
                },
            ),
            self._tool_get_offer_details,
        )
        self._register(
            ToolDefinition(
                name="log_interaction",
                description="Record user behavior event (view/click/save/purchase) for personalization.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string"},
                        "offer_id": {"type": "string"},
                        "source": {"type": "string"},
                        "link": {"type": "string"},
                        "event_type": {"type": "string", "enum": sorted(EVENT_WEIGHTS.keys())},
                        "event_id": {"type": "string"},
                    },
                    "required": ["user_id", "event_type"],
                },
            ),
            self._tool_log_interaction,
        )
        self._register(
            ToolDefinition(
                name="report_interactions",
                description="Return interaction counts split by real vs synthetic and event type.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "hours": {"type": "integer", "minimum": 0, "maximum": 24 * 365},
                    },
                },
            ),
            self._tool_report_interactions,
        )

    def _tool_search_offers(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip()
        if len(query) < 2:
            raise MCPToolError("query must be at least 2 characters")
        top_k = _clamp_int(arguments.get("top_k"), default=5, minimum=1, maximum=20)
        min_rating = _maybe_float(arguments.get("min_rating"))
        user_id = str(arguments.get("user_id") or context.get("user_id") or "").strip() or None
        normalized_query = normalize_search_query(query) or normalize_text(query)
        effective_query = normalized_query if min_rating is None else f"{normalized_query} {min_rating}+ stars high rating low price"
        rows = self.pipeline.search(query=effective_query, top_k=top_k, user_id=user_id)
        if min_rating is not None:
            rows = [r for r in rows if r.get("rating") is not None and float(r["rating"]) >= float(min_rating)]
        return {
            "query": query,
            "normalized_query": normalized_query,
            "effective_query": effective_query,
            "top_k": top_k,
            "count": len(rows),
            "results": rows,
        }

    def _tool_search_web_products(self, arguments: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.live_search_enabled:
            raise MCPToolError("Live web search is disabled by configuration.")
        if not self.settings.groq_api_key:
            raise MCPToolError("GROQ_API_KEY is required for live web search.")

        query = str(arguments.get("query", "")).strip()
        if len(query) < 2:
            raise MCPToolError("query must be at least 2 characters")
        normalized_query = normalize_search_query(query) or normalize_text(query)
        top_k = _clamp_int(
            arguments.get("top_k"),
            default=min(5, self.settings.live_search_max_results),
            minimum=1,
            maximum=max(1, self.settings.live_search_max_results),
        )
        country = str(arguments.get("country") or self.settings.live_search_country).strip().lower()

        try:
            from groq import Groq  # type: ignore
        except Exception as exc:
            raise MCPToolError(f"groq_sdk_unavailable:{exc}") from exc

        system_prompt = (
            "You are a live web shopping researcher. "
            "Use web search to find currently available product offers and return strict JSON only.\n"
            "Schema:\n"
            "{"
            '"offers":[{"title":str,"source":str,"link":str,"price_pkr":number|null,"rating":number|null,'
            '"review_count":number|null,"availability":str|null,"image":str|null,"reason":str|null}],'
            '"summary":str'
            "}\n"
            "Rules:\n"
            "- Prefer direct e-commerce listing pages.\n"
            "- Keep only most relevant offers for the user query.\n"
            "- Use null for unknown numeric values.\n"
            "- Do not return markdown."
        )
        user_prompt = (
            f"Query: {normalized_query}\n"
            f"Top offers required: {top_k}\n"
            f"Country preference: {country}\n"
            "Focus on low-price, high-rating offers when possible."
        )

        client = Groq(api_key=self.settings.groq_api_key)
        try:
            response = client.chat.completions.create(
                model=self.settings.live_search_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                max_tokens=1400,
                search_settings={"country": country},
            )
        except Exception as exc:
            raise MCPToolError(f"live_web_search_request_failed:{exc}") from exc

        text = (response.choices[0].message.content or "").strip()
        try:
            payload = _extract_json_payload(text)
        except Exception as exc:
            raise MCPToolError(f"live_web_search_parse_failed:{exc}") from exc

        raw_offers = payload.get("offers")
        if not isinstance(raw_offers, list):
            raw_offers = []

        offers: list[dict[str, Any]] = []
        for row in raw_offers[:top_k]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title", "")).strip()
            link = str(row.get("link", "")).strip()
            source = str(row.get("source", "")).strip().lower() or "web"
            if not title or not link:
                continue
            offers.append(
                {
                    "title": title,
                    "source": source,
                    "link": link,
                    "price_pkr": _maybe_float(row.get("price_pkr")),
                    "rating": _maybe_float(row.get("rating")),
                    "review_count": int(_maybe_float(row.get("review_count")) or 0) or None,
                    "availability": str(row.get("availability", "")).strip() or None,
                    "image": str(row.get("image", "")).strip() or None,
                    "reason": str(row.get("reason", "")).strip() or None,
                    "verification_status": "unverified",
                    "verified_fields": [],
                    "verified_at": None,
                }
            )

        verify_top_n = max(0, int(self.settings.live_search_verify_top_n))
        if verify_top_n > 0 and offers:
            for idx, offer in enumerate(offers[:verify_top_n]):
                verified_fields: list[str] = []
                try:
                    inspected = self._tool_inspect_product_page(
                        {
                            "link": offer.get("link"),
                            "source": offer.get("source"),
                            "title_hint": offer.get("title"),
                        },
                        {},
                    )
                    for field in ("rating", "review_count", "price_pkr", "availability", "image", "images"):
                        inspected_value = inspected.get(field)
                        if inspected_value in (None, "", []):
                            continue
                        if offer.get(field) in (None, "", []):
                            offer[field] = inspected_value
                        offer[f"page_{field}"] = inspected_value
                        verified_fields.append(field)
                    offer["verification_status"] = "verified" if verified_fields else "inspected_no_signals"
                    offer["verified_fields"] = sorted(set(verified_fields))
                    offer["verified_at"] = datetime.now(timezone.utc).isoformat()
                    offer["inspected_url"] = inspected.get("final_url") or inspected.get("link")
                except Exception as exc:
                    logger.warning("live_offer_verification_failed idx=%s link=%s error=%s", idx, offer.get("link"), exc)
                    offer["verification_status"] = "verify_failed"
                    offer["verified_fields"] = []
                    offer["verified_at"] = datetime.now(timezone.utc).isoformat()
                    offer["verify_error"] = str(exc)

        search_refs: list[dict[str, Any]] = []
        tools = getattr(response.choices[0].message, "executed_tools", None) or []
        for tool in tools:
            raw_results = getattr(tool, "search_results", None)
            if raw_results is None:
                continue
            if isinstance(raw_results, dict):
                items = list(raw_results.get("results") or [])
            elif hasattr(raw_results, "results"):
                items = list(getattr(raw_results, "results") or [])
            else:
                try:
                    items = list(raw_results)
                except TypeError:
                    items = []
            for item in items[:20]:
                if isinstance(item, dict):
                    title = item.get("title")
                    url = item.get("url")
                    score = item.get("score")
                else:
                    title = getattr(item, "title", None)
                    url = getattr(item, "url", None)
                    score = getattr(item, "score", None)
                if not url:
                    continue
                search_refs.append(
                    {
                        "title": str(title or "").strip() or None,
                        "url": str(url),
                        "score": float(score) if score not in (None, "") else None,
                    }
                )

        return {
            "query": query,
            "normalized_query": normalized_query,
            "top_k": top_k,
            "country": country,
            "count": len(offers),
            "summary": str(payload.get("summary", "")).strip() or None,
            "offers": offers,
            "sources": search_refs[:40],
            "model": self.settings.live_search_model,
        }

    def _tool_inspect_product_page(self, arguments: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
        link = str(arguments.get("link", "")).strip()
        if not link:
            raise MCPToolError("link is required")
        source = str(arguments.get("source", "")).strip().lower() or None
        title_hint = str(arguments.get("title_hint", "")).strip() or None

        if link.startswith("/store/products/"):
            product_id = link.rsplit("/", 1)[-1].strip()
            doc = self.db[self.settings.marketplace_seller_products_collection].find_one(
                {"product_id": product_id, "status": "active"},
                get_marketplace_projection(),
            )
            if doc:
                offer = seller_product_to_search_doc(doc)
                report = self._sales_report_for_offer(offer)
                return {
                    "link": link,
                    "final_url": link,
                    "source": offer.get("source"),
                    "title": offer.get("title"),
                    "price_pkr": offer.get("price_pkr"),
                    "shipping_price_pkr": offer.get("shipping_pkr"),
                    "shipping_summary": "Seller-managed marketplace listing. Delivery details are provided by the seller listing fields.",
                    "delivery_info": "This product is listed directly on the marketplace by a seller.",
                    "warranty_info": None,
                    "availability": "In stock" if offer.get("in_stock") else "Out of stock",
                    "image": offer.get("image"),
                    "images": list(offer.get("images") or []),
                    "review_count": 0,
                    "rating": None,
                    "units_sold": report.get("units_sold", 0),
                    "order_count": report.get("order_count", 0),
                    "revenue_pkr": report.get("revenue_pkr", 0.0),
                    "predicted_app_rating": report.get("predicted_app_rating"),
                    "predicted_demand_score": report.get("predicted_demand_score"),
                    "seasonal_relevance_score": report.get("seasonal_relevance_score"),
                    "best_month_labels": report.get("best_month_labels") or [],
                    "page_type": "internal_marketplace",
                    "page_warning": None,
                    "has_review_signals": False,
                    "inspection_notes": ["Used stored marketplace seller listing instead of external page inspection."],
                }

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        final_url, html = self._fetch_html_safe(link, headers)
        soup = BeautifulSoup(html, "html.parser")
        page_title = (soup.title.string.strip() if soup.title and soup.title.string else None) or title_hint
        domain = urlparse(final_url).netloc.lower()
        resolved_source = source or domain

        rating_candidates: list[float] = []
        review_count_candidates: list[int] = []
        price_candidates: list[float] = []
        availability_signals: list[str] = []
        shipping_price_candidates: list[float] = []
        image_candidates: list[str] = []
        review_snippets: list[str] = []
        extraction_notes: list[str] = []
        shipping_snippets: list[str] = []
        delivery_info: str | None = None
        warranty_info: str | None = None

        def _collect_image(raw: Any) -> None:
            for img in normalize_image_gallery(raw, base_url=final_url):
                image_candidates.append(img)

        # JSON-LD product parsing (highest-quality structured source)
        products = _extract_jsonld_products(soup)
        for product in products:
            agg = product.get("aggregateRating") or {}
            rating_val = _safe_float(agg.get("ratingValue"))
            if rating_val is not None and 0.0 <= rating_val <= 5.0:
                rating_candidates.append(rating_val)
            review_count = _safe_int(agg.get("reviewCount"))
            if review_count is not None and review_count >= 0:
                review_count_candidates.append(review_count)

            offers = product.get("offers")
            _collect_image(product.get("image"))
            offer_nodes = offers if isinstance(offers, list) else [offers]
            for offer in offer_nodes:
                if not isinstance(offer, dict):
                    continue
                _collect_image(offer.get("image"))
                price_raw = offer.get("price")
                if price_raw not in (None, ""):
                    try:
                        price_num = float(price_raw)
                    except (TypeError, ValueError):
                        price_num = None
                    if price_num is not None:
                        price_candidates.append(price_num)
                availability = offer.get("availability")
                if availability:
                    availability_signals.append(str(availability))
                shipping_details = offer.get("shippingDetails") or offer.get("shippingDetail")
                shipping_nodes = shipping_details if isinstance(shipping_details, list) else [shipping_details]
                for shipping_node in shipping_nodes:
                    if not isinstance(shipping_node, dict):
                        continue
                    shipping_rate = shipping_node.get("shippingRate")
                    rate_value = shipping_rate
                    if isinstance(shipping_rate, dict):
                        rate_value = (
                            shipping_rate.get("value")
                            or shipping_rate.get("price")
                            or shipping_rate.get("amount")
                        )
                    parsed_rate = _safe_float(rate_value)
                    if parsed_rate is not None and 0 <= parsed_rate <= 100_000:
                        shipping_price_candidates.append(parsed_rate)
                    snippet_parts = [
                        shipping_node.get("name"),
                        shipping_node.get("shippingLabel"),
                        shipping_node.get("description"),
                        shipping_node.get("deliveryTime"),
                        shipping_node.get("deliveryLeadTime"),
                    ]
                    snippet_text = _clean_text_snippet(" ".join(str(part or "") for part in snippet_parts if part))
                    if snippet_text:
                        shipping_snippets.append(snippet_text)

            reviews = product.get("review")
            if isinstance(reviews, list):
                review_count_candidates.append(len(reviews))
                for row in reviews[:5]:
                    if isinstance(row, dict):
                        body = str(row.get("reviewBody", "")).strip()
                        if body:
                            review_snippets.append(body)
            elif isinstance(reviews, dict):
                body = str(reviews.get("reviewBody", "")).strip()
                if body:
                    review_snippets.append(body)

        if products:
            extraction_notes.append(f"jsonld_products={len(products)}")

        # Microdata/meta fallback
        for selector in ['meta[itemprop="ratingValue"]', '[itemprop="ratingValue"]']:
            for node in soup.select(selector):
                value = node.get("content") if hasattr(node, "get") else None
                if value in (None, ""):
                    value = node.get_text(" ", strip=True)
                parsed = _safe_float(value)
                if parsed is not None and 0.0 <= parsed <= 5.0:
                    rating_candidates.append(parsed)

        for selector in ['meta[itemprop="reviewCount"]', '[itemprop="reviewCount"]']:
            for node in soup.select(selector):
                value = node.get("content") if hasattr(node, "get") else None
                if value in (None, ""):
                    value = node.get_text(" ", strip=True)
                parsed = _safe_int(value)
                if parsed is not None and parsed >= 0:
                    review_count_candidates.append(parsed)

        for selector in [
            'meta[property="og:image"]',
            'meta[name="twitter:image"]',
            'meta[itemprop="image"]',
        ]:
            for node in soup.select(selector):
                _collect_image(node.get("content") if hasattr(node, "get") else None)

        for selector in ['img[itemprop="image"]', 'img.product-image-photo', 'img[class*="product"]', 'img[src]']:
            for node in soup.select(selector)[:12]:
                if hasattr(node, "get"):
                    for attr in ("src", "data-src", "data-lazy-src", "srcset", "data-srcset"):
                        _collect_image(node.get(attr))

        source_gallery_before = len(image_candidates)
        for node in soup.select("picture source, source[srcset]")[:12]:
            if hasattr(node, "get"):
                for attr in ("srcset", "data-srcset", "src"):
                    _collect_image(node.get(attr))
        if len(image_candidates) > source_gallery_before:
            extraction_notes.append("source_gallery_detected")

        # Text-pattern fallback from visible text
        text = soup.get_text(" ", strip=True)
        text_low = text.lower()
        for match in re.finditer(r"([0-5](?:\.\d+)?)\s*(?:/5|stars?|star rating)", text_low):
            parsed = _safe_float(match.group(1))
            if parsed is not None and 0.0 <= parsed <= 5.0:
                rating_candidates.append(parsed)

        for match in re.finditer(r"(\d[\d,]{0,6})\s*(?:reviews?|ratings?)", text_low):
            parsed = _safe_int(match.group(1).replace(",", ""))
            if parsed is not None and parsed >= 0:
                review_count_candidates.append(parsed)

        price_candidates.extend(_extract_price_candidates_from_text(text[:250000]))
        text_shipping_price, shipping_summary, delivery_info_text = _extract_shipping_signals(text[:250000])
        if text_shipping_price is not None:
            shipping_price_candidates.append(text_shipping_price)
        if shipping_summary:
            shipping_snippets.append(shipping_summary)
        if delivery_info_text:
            delivery_info = delivery_info_text
        warranty_info = _extract_warranty_info(text[:250000])

        if "sold out" in text_low or "out of stock" in text_low:
            availability_signals.append("out_of_stock")
        if "in stock" in text_low or "available" in text_low:
            availability_signals.append("in_stock")

        for selector in ['[itemprop="reviewBody"]', '[class*="review"] p', '[class*="testimonial"] p']:
            for node in soup.select(selector):
                snippet = " ".join(node.get_text(" ", strip=True).split())
                if snippet and len(snippet) >= 30:
                    review_snippets.append(snippet)
                if len(review_snippets) >= 6:
                    break
            if len(review_snippets) >= 6:
                break

        # Deduplicate snippets
        seen_snippets: set[str] = set()
        deduped_snippets: list[str] = []
        for snippet in review_snippets:
            key = snippet.lower()
            if key in seen_snippets:
                continue
            seen_snippets.add(key)
            deduped_snippets.append(snippet)
            if len(deduped_snippets) >= 5:
                break

        rating = max(rating_candidates) if rating_candidates else None
        review_count = max(review_count_candidates) if review_count_candidates else None
        price_pkr = min(price_candidates) if price_candidates else None
        shipping_price_pkr = min(shipping_price_candidates) if shipping_price_candidates else None
        availability = availability_signals[-1] if availability_signals else None
        deduped_images = list(dict.fromkeys(image_candidates))
        image = deduped_images[0] if deduped_images else None
        deduped_shipping_snippets = list(dict.fromkeys(snippet for snippet in shipping_snippets if snippet))
        shipping_summary = deduped_shipping_snippets[0] if deduped_shipping_snippets else None
        if image:
            extraction_notes.append("image_detected")
        if shipping_summary or shipping_price_pkr is not None:
            extraction_notes.append("shipping_detected")
        if warranty_info:
            extraction_notes.append("warranty_detected")
        page_type, page_warning = _classify_page_type(
            final_url=final_url,
            page_title=page_title,
            title_hint=title_hint,
            jsonld_product_count=len(products),
            text=text[:12000],
        )
        detail_quality = "high" if page_type == "product" else ("low" if page_type == "listing" else "medium")
        has_review_signals = bool((rating is not None) or (review_count is not None) or deduped_snippets) and page_type != "listing"
        if page_warning:
            extraction_notes.append("listing_page_detected")

        return {
            "link": link,
            "final_url": final_url,
            "source": resolved_source,
            "domain": domain,
            "title": page_title,
            "page_type": page_type,
            "detail_quality": detail_quality,
            "page_warning": page_warning,
            "rating": rating,
            "review_count": review_count,
            "price_pkr": price_pkr,
            "shipping_price_pkr": shipping_price_pkr,
            "shipping_summary": shipping_summary,
            "delivery_info": delivery_info or shipping_summary,
            "warranty_info": warranty_info,
            "availability": availability,
            "image": image,
            "images": deduped_images,
            "review_snippets": deduped_snippets,
            "has_review_signals": has_review_signals,
            "notes": extraction_notes,
        }

    def _tool_get_offer_details(self, arguments: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
        offer_id = str(arguments.get("offer_id", "")).strip() or None
        source = str(arguments.get("source", "")).strip().lower() or None
        link = str(arguments.get("link", "")).strip() or None
        if not offer_id:
            if source and link:
                offer_id = offer_id_from_source_link(source, link)
            else:
                raise MCPToolError("offer_id or (source + link) is required")
        doc = self.db[self.settings.normalized_collection].find_one(
            {"offer_id": offer_id},
            {
                "_id": 0,
                "offer_id": 1,
                "title": 1,
                "title_normalized": 1,
                "link": 1,
                "source": 1,
                "image": 1,
                "images": 1,
                "price_pkr": 1,
                "shipping_pkr": 1,
                "in_stock": 1,
                "rating": 1,
                "review_count": 1,
                "brand": 1,
                "model": 1,
                "storage_gb": 1,
                "ram_gb": 1,
                "category": 1,
                "subcategory": 1,
                "last_scraped": 1,
                "last_seen_at": 1,
            },
        )
        if not doc:
            seller_doc = self.db[self.settings.marketplace_seller_products_collection].find_one(
                {"offer_id": offer_id, "status": "active"},
                get_marketplace_projection(),
            )
            if not seller_doc and link and link.startswith("/store/products/"):
                seller_doc = self.db[self.settings.marketplace_seller_products_collection].find_one(
                    {"product_id": link.rsplit("/", 1)[-1].strip(), "status": "active"},
                    get_marketplace_projection(),
                )
            if not seller_doc:
                return {"found": False, "offer_id": offer_id}
            offer = seller_product_to_search_doc(seller_doc)
            offer.update(self._sales_report_for_offer(offer))
            offer = self._price_range_for_offer(apply_display_source_fields(offer))
            return {"found": True, "offer": offer}
        product_id = f"scraped_{offer_id}"
        offer = dict(doc)
        offer["product_id"] = product_id
        offer.update(self._sales_report_for_offer(offer))
        if not offer.get("best_month_labels"):
            defaults = derive_prediction_defaults(offer)
            offer.update({k: offer.get(k) if offer.get(k) not in (None, [], "") else v for k, v in defaults.items()})
        offer = self._price_range_for_offer(apply_display_source_fields(offer))
        return {"found": True, "offer": offer}

    def _tool_log_interaction(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        user_id = str(arguments.get("user_id") or context.get("user_id") or "").strip()
        if not user_id:
            raise MCPToolError("user_id is required")
        event_type = str(arguments.get("event_type", "")).strip().lower()
        if event_type not in EVENT_WEIGHTS:
            raise MCPToolError(f"event_type must be one of: {sorted(EVENT_WEIGHTS)}")
        offer_id = str(arguments.get("offer_id", "")).strip() or None
        source = str(arguments.get("source", "")).strip().lower() or None
        link = str(arguments.get("link", "")).strip() or None
        event_id = str(arguments.get("event_id", "")).strip() or None
        doc = log_interaction(
            settings=self.settings,
            user_id=user_id,
            event_type=event_type,
            offer_id=offer_id,
            source=source,
            link=link,
            event_id=event_id,
            db=self.db,
        )
        return {"logged": True, "interaction": doc}

    def _tool_report_interactions(self, arguments: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
        hours = _clamp_int(arguments.get("hours"), default=0, minimum=0, maximum=24 * 365)
        col = self.db[self.settings.interactions_collection]
        base_query: dict[str, Any] = {}
        if hours > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            base_query["event_ts"] = {"$gte": cutoff}

        real_filter = {"$or": [{"is_synthetic": {"$exists": False}}, {"is_synthetic": False}]}
        synthetic_filter = {"is_synthetic": True}

        def _join(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
            if not base:
                return dict(extra)
            return {"$and": [base, extra]}

        total = col.count_documents(base_query)
        real_total = col.count_documents(_join(base_query, real_filter))
        synthetic_total = col.count_documents(_join(base_query, synthetic_filter))
        breakdown = list(
            col.aggregate(
                (
                    ([{"$match": base_query}] if base_query else [])
                    + [
                        {
                            "$group": {
                                "_id": {
                                    "event_type": "$event_type",
                                    "is_synthetic": {"$ifNull": ["$is_synthetic", False]},
                                },
                                "count": {"$sum": 1},
                            }
                        },
                        {"$sort": {"_id.event_type": 1, "_id.is_synthetic": 1}},
                    ]
                )
            )
        )

        return {
            "hours": hours,
            "total": total,
            "real_total": real_total,
            "synthetic_total": synthetic_total,
            "event_breakdown": [
                {
                    "event_type": item["_id"]["event_type"],
                    "is_synthetic": bool(item["_id"]["is_synthetic"]),
                    "count": int(item["count"]),
                }
                for item in breakdown
            ],
        }

    def _log_tool_call(
        self,
        *,
        conversation_id: str | None,
        user_id: str | None,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any] | None,
        started_at: datetime,
        error: str | None,
        error_type: str | None,
        domain: str | None,
    ) -> None:
        finished = datetime.now(timezone.utc)
        doc = {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "tool_name": tool_name,
            "domain": domain,
            "arguments": _iso(arguments),
            "result": result,
            "error": error,
            "error_type": error_type,
            "duration_ms": int((finished - started_at).total_seconds() * 1000),
            "ts": finished,
        }
        try:
            self.db[self.settings.assistant_tool_logs_collection].insert_one(doc)
        except Exception:
            logger.exception("tool_call_log_failed")
