import re
from datetime import datetime, timezone
from hashlib import sha1
import json
from urllib.parse import urljoin


KNOWN_BRANDS = [
    "apple",
    "samsung",
    "xiaomi",
    "oppo",
    "vivo",
    "infinix",
    "realme",
    "nokia",
    "tecno",
    "huawei",
    "dell",
    "hp",
    "lenovo",
    "asus",
]

SEARCH_QUERY_TOKEN_REWRITES = {
    "fin": "find",
    "fnd": "find",
    "srch": "search",
    "gamming": "gaming",
    "gamimg": "gaming",
    "gamng": "gaming",
    "wirless": "wireless",
    "wireles": "wireless",
    "blutooth": "bluetooth",
    "bluetooh": "bluetooth",
    "earbud": "earbuds",
    "earbudss": "earbuds",
    "headphone": "headphones",
    "smartwach": "smartwatch",
    "pak": "pakistan",
}

SEARCH_QUERY_DROP_TOKENS = {
    "a",
    "an",
    "find",
    "for",
    "in",
    "me",
    "please",
    "product",
    "products",
    "search",
    "show",
}

SEARCH_QUERY_LOCATION_TOKENS = {
    "pakistan",
}

IMAGE_GALLERY_LIMIT = 8

IMAGE_PLACEHOLDER_MARKERS = (
    "placeholder.com",
    "via.placeholder.com",
    "/placeholder",
    "no-image",
    "no_image",
    "image-not-available",
    "coming-soon",
    "coming_soon",
)


def normalize_price_to_pkr(raw_price: str | None) -> float | None:
    if not raw_price:
        return None
    cleaned = raw_price.lower()
    # Choose the largest numeric segment to avoid selecting model numbers.
    matches = re.findall(r"\d[\d,]*(?:\.\d+)?", cleaned)
    if not matches:
        return None
    values = [float(m.replace(",", "")) for m in matches]
    # Keep realistic PKR amounts only.
    plausible = [v for v in values if v >= 100]
    if not plausible:
        return None
    return float(max(plausible))


def normalize_text(value: str) -> str:
    lowered = value.lower().strip()
    lowered = re.sub(r"[^a-z0-9\s\-\+\.]", " ", lowered)
    return " ".join(lowered.split())


def normalize_search_query(value: str) -> str:
    text = normalize_text(value)
    if not text:
        return text

    # Normalize a few common product-search typos and remove generic command words.
    text = text.replace("ear buds", "earbuds").replace("air pods", "airpods").replace("smart watch", "smartwatch")
    rewritten_tokens: list[str] = []
    for raw_token in text.split():
        token = SEARCH_QUERY_TOKEN_REWRITES.get(raw_token, raw_token)
        if token in SEARCH_QUERY_DROP_TOKENS or token in SEARCH_QUERY_LOCATION_TOKENS:
            continue
        rewritten_tokens.append(token)

    if rewritten_tokens:
        return " ".join(rewritten_tokens)
    return text


def parse_last_scraped(value: str | datetime | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc)
        return value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    # Fast-path for ISO timestamps, including timezone offsets.
    try:
        iso_candidate = text.replace("Z", "+00:00")
        parsed_iso = datetime.fromisoformat(iso_candidate)
        if parsed_iso.tzinfo is None:
            return parsed_iso.replace(tzinfo=timezone.utc)
        return parsed_iso.astimezone(timezone.utc)
    except ValueError:
        pass
    formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d")
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_rating(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if 0.0 <= v <= 5.0:
            return v
    txt = normalize_text(str(value))
    m = re.search(r"([0-5](?:\.\d+)?)", txt)
    if not m:
        return None
    v = float(m.group(1))
    if 0.0 <= v <= 5.0:
        return v
    return None


def parse_review_count(value) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return max(value, 0)
    m = re.search(r"\d[\d,]*", str(value))
    if not m:
        return 0
    return max(int(m.group(0).replace(",", "")), 0)


def detect_stock(price_text: str | None, title: str | None) -> bool:
    hay = normalize_text(f"{price_text or ''} {title or ''}")
    blocked = ("sold out", "out of stock", "not available", "unavailable")
    return not any(x in hay for x in blocked)


def extract_brand(title: str) -> str | None:
    t = normalize_text(title)
    tokens = set(t.split())
    for brand in KNOWN_BRANDS:
        if brand in tokens:
            return brand
    return None


def extract_storage_gb(title: str) -> int | None:
    t = normalize_text(title)
    m = re.search(r"(\d+)\s*gb", t)
    if m:
        return int(m.group(1))
    return None


def extract_ram_gb(title: str) -> int | None:
    t = normalize_text(title)
    m = re.search(r"(\d+)\s*gb\s*ram", t)
    if not m:
        return None
    return int(m.group(1))


def extract_model(title: str, brand: str | None) -> str | None:
    t = normalize_text(title)
    if not brand:
        return None
    t = t.replace(brand, "").strip()
    parts = t.split()
    if not parts:
        return None
    return " ".join(parts[:4])


def offer_id_from_source_link(source: str, link: str) -> str:
    digest = sha1(f"{source}|{link}".encode("utf-8")).hexdigest()
    return digest


def canonical_key(brand: str | None, model: str | None, storage_gb: int | None, ram_gb: int | None) -> str:
    payload = f"{brand or 'unknown'}|{model or 'unknown'}|{storage_gb or -1}|{ram_gb or -1}"
    return sha1(payload.encode("utf-8")).hexdigest()


def stable_fingerprint(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return sha1(encoded.encode("utf-8")).hexdigest()


def parse_srcset_urls(value: str | None) -> list[str]:
    if not value:
        return []
    out: list[str] = []
    for item in str(value).split(","):
        token = item.strip().split(" ")[0].strip()
        if token:
            out.append(token)
    return out


def normalize_image_url(value: str | None, *, base_url: str | None = None) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    elif raw.startswith("/") and base_url:
        raw = urljoin(base_url, raw)
    lowered = raw.lower()
    if lowered.startswith("data:") or lowered.startswith("javascript:") or lowered == "about:blank":
        return None
    if any(marker in lowered for marker in IMAGE_PLACEHOLDER_MARKERS):
        return None
    return raw


def normalize_image_gallery(
    values,
    *,
    base_url: str | None = None,
    limit: int = IMAGE_GALLERY_LIMIT,
) -> list[str]:
    if values is None:
        return []
    raw_values = values if isinstance(values, list) else [values]
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        for candidate in parse_srcset_urls(raw) if isinstance(raw, str) and "," in raw else [raw]:
            normalized = normalize_image_url(candidate, base_url=base_url)
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(normalized)
            if len(out) >= limit:
                return out
    return out


def primary_image_from_gallery(values, *, base_url: str | None = None) -> str | None:
    gallery = normalize_image_gallery(values, base_url=base_url, limit=1)
    return gallery[0] if gallery else None
