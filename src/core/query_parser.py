import re
from dataclasses import dataclass

from src.core.normalize import normalize_search_query, normalize_text


KNOWN_BRANDS = {
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
    "hp",
    "dell",
    "lenovo",
    "asus",
}


@dataclass
class ParsedQuery:
    cleaned_query: str
    brand: str | None
    max_price_pkr: float | None
    min_rating: float | None
    storage_gb: int | None
    ram_gb: int | None
    prefer_value: bool


def _extract_price(q: str) -> float | None:
    patterns = [
        r"(?:under|below|less than|max)\s*(?:pkr|rs\.?|rupees)?\s*([0-9][0-9,]*)",
        r"([0-9][0-9,]*)\s*(?:pkr|rs\.?|rupees)",
    ]
    for pat in patterns:
        m = re.search(pat, q)
        if m:
            return float(m.group(1).replace(",", ""))
    return None


def _extract_size_gb(q: str, key: str) -> int | None:
    m = re.search(rf"(\d+)\s*{key}", q)
    if m:
        return int(m.group(1))
    return None


def parse_query(query: str) -> ParsedQuery:
    q = normalize_search_query(query)
    tokens = set(q.split())
    brand = next((b for b in KNOWN_BRANDS if b in tokens), None)
    max_price = _extract_price(q)
    rating_match = re.search(r"([1-5](?:\.\d+)?)\s*(?:\+|plus)?\s*(?:stars?|rating)", q)
    min_rating = float(rating_match.group(1)) if rating_match else None
    storage = _extract_size_gb(q, "gb")
    ram = _extract_size_gb(q, "ram")
    prefer_value = any(
        phrase in q
        for phrase in (
            "best value",
            "high rating low price",
            "best rated cheap",
            "cheap and good",
            "value for money",
        )
    )
    return ParsedQuery(
        cleaned_query=q,
        brand=brand,
        max_price_pkr=max_price,
        min_rating=min_rating,
        storage_gb=storage,
        ram_gb=ram,
        prefer_value=prefer_value,
    )
