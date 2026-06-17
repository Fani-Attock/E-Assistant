import math
from datetime import datetime, timezone
from typing import Iterable


SOURCE_RELIABILITY = {
    "daraz": 0.9,
    "daraz.pk": 0.9,
    "ishopping": 0.9,
    "ishopping.pk": 0.9,
    "shophive": 0.9,
}


def _price_component(total_price_pkr: float | None) -> float:
    if not total_price_pkr or total_price_pkr <= 0:
        return 0.0
    return 1.0 / (1.0 + math.log1p(total_price_pkr))


def _freshness_component(last_scraped_dt: datetime | None, now: datetime) -> float:
    if last_scraped_dt is None:
        return 0.0
    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    scraped_utc = (
        last_scraped_dt.astimezone(timezone.utc)
        if last_scraped_dt.tzinfo
        else last_scraped_dt.replace(tzinfo=timezone.utc)
    )
    delta_hours = max((now_utc - scraped_utc).total_seconds() / 3600.0, 0.0)
    return 1.0 / (1.0 + delta_hours / 24.0)


def rank_offer(
    *,
    total_price_pkr: float | None,
    match_score: float,
    rating: float | None,
    review_count: int | None,
    in_stock: bool,
    source: str | None,
    last_scraped_dt: datetime | None,
    now: datetime,
    prefer_value: bool = False,
) -> float:
    price = _price_component(total_price_pkr)
    fresh = _freshness_component(last_scraped_dt, now)
    stock = 1.0 if in_stock else 0.0
    source_score = SOURCE_RELIABILITY.get((source or "").lower(), 0.75)
    rating_component = 0.0 if rating is None else max(min(rating / 5.0, 1.0), 0.0)
    social_proof = 0.0 if not review_count else min(math.log1p(review_count) / 8.0, 1.0)
    if prefer_value:
        return (
            0.35 * match_score
            + 0.25 * price
            + 0.2 * rating_component
            + 0.05 * social_proof
            + 0.1 * fresh
            + 0.03 * stock
            + 0.02 * source_score
        )
    return (
        0.45 * match_score
        + 0.2 * price
        + 0.15 * rating_component
        + 0.05 * social_proof
        + 0.1 * fresh
        + 0.03 * stock
        + 0.02 * source_score
    )


def sort_results(rows: Iterable[dict]) -> list[dict]:
    return sorted(rows, key=lambda x: x.get("rank_score", 0.0), reverse=True)
