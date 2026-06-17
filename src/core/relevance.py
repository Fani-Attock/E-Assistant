from __future__ import annotations

from typing import Iterable

from src.core.normalize import normalize_search_query, normalize_text


QUERY_STOPWORDS = {
    "about",
    "above",
    "and",
    "are",
    "asking",
    "best",
    "buy",
    "capacity",
    "cheap",
    "find",
    "for",
    "give",
    "good",
    "high",
    "im",
    "in",
    "is",
    "low",
    "me",
    "more",
    "online",
    "pakistan",
    "price",
    "rating",
    "ratings",
    "review",
    "reviews",
    "search",
    "show",
    "that",
    "the",
    "under",
    "want",
    "with",
}

AUDIO_PRODUCT_PHRASES = (
    "earbud",
    "earbuds",
    "earphone",
    "earphones",
    "headphone",
    "headphones",
    "headset",
    "airpod",
    "airpods",
    "neckband",
    "buds",
)

WATCH_PRODUCT_PHRASES = (
    "watch",
    "watches",
    "smartwatch",
    "smart watch",
    "ultra smart watch",
    "watch 7",
    "watch 8",
    "t800",
    "t900",
    "hw22",
    "hk9",
)

GENERIC_LISTING_PHRASES = (
    "price list",
    "best gaming earbuds in pakistan",
    "best earbuds in pakistan",
    "buyer guide",
    "buyers guide",
    "buyer s guide",
    "various brands",
    "/tag/",
    "/category/",
    "/pricelist/",
)


def _candidate_text(row: dict) -> str:
    return normalize_text(
        " ".join(
            str(row.get(key) or "")
            for key in (
                "title",
                "title_normalized",
                "brand",
                "model",
                "category",
                "subcategory",
                "source",
                "link",
                "specifications",
                "reason",
            )
        )
    )


def _query_text(query: str) -> str:
    text = normalize_search_query(query)
    text = text.replace("powerbank", "power bank").replace("powerbanks", "power banks")
    return text


def _tokens(text: str) -> set[str]:
    return {token for token in normalize_text(text).split() if token and token not in QUERY_STOPWORDS}


def _has_any_phrase(text: str, phrases: Iterable[str]) -> bool:
    padded = f" {text} "
    return any(f" {normalize_text(phrase)} " in padded for phrase in phrases)


def query_requires_power_bank(query: str) -> bool:
    q = _query_text(query)
    tokens = set(q.split())
    return (
        ("power" in tokens and ("bank" in tokens or "banks" in tokens))
        or "portable charger" in q
        or "battery pack" in q
    )


def query_requires_audio_product(query: str) -> bool:
    q = _query_text(query)
    tokens = set(q.split())
    return any(term in tokens for term in AUDIO_PRODUCT_PHRASES) or _has_any_phrase(q, AUDIO_PRODUCT_PHRASES)


def query_requires_watch(query: str) -> bool:
    q = _query_text(query)
    tokens = set(q.split())
    return "watch" in tokens or "watches" in tokens or _has_any_phrase(q, WATCH_PRODUCT_PHRASES)


def _looks_like_listing_or_article(text: str) -> bool:
    return _has_any_phrase(text, GENERIC_LISTING_PHRASES)


def _is_power_bank_result(text: str) -> bool:
    tokens = set(text.split())
    positive = (
        ("power" in tokens and ("bank" in tokens or "banks" in tokens))
        or "powerbank" in tokens
        or "powerbanks" in tokens
        or _has_any_phrase(
            text,
            (
                "portable charger",
                "power station",
                "battery pack",
                "charging bank",
            ),
        )
    )
    if not positive:
        return False

    negative_phrases = (
        "buyer guide",
        "buyers guide",
        "buyer s guide",
        "best power banks in pakistan",
        "various brands",
        "/tag/",
        "/collections/",
        "/category/",
        "power cable",
        "pd cable",
        "charging cable",
        "type c cable",
        "vacuum cleaner",
        "water cooler",
        "earbuds",
        "earphones",
        "headphones",
        "smart watch",
        "suit with earphones",
    )
    return not _has_any_phrase(text, negative_phrases)


def _is_audio_product_result(text: str) -> bool:
    tokens = set(text.split())
    positive = any(term in tokens for term in AUDIO_PRODUCT_PHRASES) or _has_any_phrase(text, AUDIO_PRODUCT_PHRASES)
    if not positive:
        return False
    negative_phrases = GENERIC_LISTING_PHRASES + (
        "smart watch",
        "smart watches",
        "ultra smart watch",
        "watch 7",
        "watch 8",
        "watch with earbuds",
        "watch with earphones",
        "earbuds with watch",
        "7 in 1",
        "in 1 set",
        "set with",
        "suit with",
        "combo",
        "bundle",
        "portable power station",
        "power bank",
        "charging cable",
        "type c cable",
    )
    return not _has_any_phrase(text, negative_phrases)


def _is_watch_result(text: str) -> bool:
    tokens = set(text.split())
    positive = "watch" in tokens or "watches" in tokens or _has_any_phrase(text, WATCH_PRODUCT_PHRASES)
    if not positive:
        return False
    negative_phrases = GENERIC_LISTING_PHRASES + (
        "earbud",
        "earbuds",
        "earphone",
        "earphones",
        "headphone",
        "headphones",
        "headset",
        "portable power station",
        "power bank",
    )
    return not _has_any_phrase(text, negative_phrases)


def row_matches_required_product_concept(query: str, row: dict) -> bool:
    text = _candidate_text(row)
    if query_requires_power_bank(query):
        return _is_power_bank_result(text)
    if query_requires_audio_product(query):
        return _is_audio_product_result(text)
    if query_requires_watch(query):
        return _is_watch_result(text)
    return True


def lexical_relevance_score(query: str, row: dict) -> float:
    q_tokens = _tokens(_query_text(query))
    if not q_tokens:
        return 0.0
    row_text = _candidate_text(row)
    row_tokens = _tokens(row_text)
    if not row_tokens:
        return 0.0
    if not row_matches_required_product_concept(query, row):
        return 0.0
    overlap = len(q_tokens.intersection(row_tokens))
    return overlap / max(1, len(q_tokens))


def filter_relevant_results(query: str, rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    scored: list[tuple[float, int, dict]] = []
    requires_strict_concept = any(
        (
            query_requires_power_bank(query),
            query_requires_audio_product(query),
            query_requires_watch(query),
        )
    )
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        score = lexical_relevance_score(query, row)
        if requires_strict_concept and score <= 0.0:
            continue
        next_row = dict(row)
        next_row["relevance_score"] = float(score)
        scored.append((score, idx, next_row))
    if requires_strict_concept:
        scored.sort(key=lambda item: (-item[0], item[1]))
    return [row for _, _, row in scored]


def query_result_relevance(query: str, rows: list[dict]) -> float:
    if not rows:
        return 0.0
    scores = [lexical_relevance_score(query, row) for row in rows[:5] if isinstance(row, dict)]
    return max(scores, default=0.0)
