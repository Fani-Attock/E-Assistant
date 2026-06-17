from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from pymongo import MongoClient, UpdateOne

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.normalize import normalize_image_gallery, primary_image_from_gallery
from src.core.settings import Settings

PRODUCT_SELECTORS = [
    ".product-item",
    "li.product-item",
]
TITLE_SELECTORS = [
    "h2.product-item-name a",
    ".product-item-link",
]
PRICE_SELECTORS = [
    'span[data-price-type="finalPrice"] .price',
    ".price-box .price",
]
RATING_TEXT_SELECTORS = [
    ".rating-summary .rating-result",
    ".product-reviews-summary .rating-result",
]
REVIEW_COUNT_SELECTORS = [
    ".reviews-actions .action.view",
    ".product-reviews-summary .reviews-actions",
]
IMAGE_SELECTORS = [
    "img.product-image-photo",
    "img",
]
IMAGE_ATTRS = ("src", "data-src", "data-lazy-src", "srcset", "data-srcset")
LOAD_MORE_SELECTORS = [
    "button.load-more",
]


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect_to_db(mongo_uri: str, db_name: str, collection_name: str):
    client = MongoClient(mongo_uri)
    db = client[db_name]
    collection = db[collection_name]
    collection.create_index("link", unique=True)
    print("MongoDB connection successful!")
    return collection


def load_categories(path: str) -> dict:
    cfg_path = Path(path)
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    categories = data.get("categories", {})
    if not categories:
        raise ValueError(f"No categories found in {path}")
    return categories


async def extract_text(scope, selectors: list[str]) -> str | None:
    for selector in selectors:
        try:
            loc = scope.locator(selector)
            if await loc.count() == 0:
                continue
            value = (await loc.first.inner_text()).strip()
            if value:
                return value
        except Exception:
            continue
    return None


async def extract_attr(scope, selectors: list[str], attr: str) -> str | None:
    for selector in selectors:
        try:
            loc = scope.locator(selector)
            if await loc.count() == 0:
                continue
            value = await loc.first.get_attribute(attr)
            if value and value.strip():
                return value.strip()
        except Exception:
            continue
    return None


async def extract_image_gallery(scope, selectors: list[str], *, base_url: str | None = None) -> list[str]:
    raw_values: list[str] = []
    for selector in selectors:
        try:
            loc = scope.locator(selector)
            count = min(await loc.count(), 6)
        except Exception:
            continue
        for idx in range(count):
            node = loc.nth(idx)
            for attr in IMAGE_ATTRS:
                try:
                    value = await node.get_attribute(attr)
                except Exception:
                    value = None
                if value and value.strip():
                    raw_values.append(value.strip())
        if raw_values:
            break
    return normalize_image_gallery(raw_values, base_url=base_url)


async def extract_rating_and_review_count(scope) -> tuple[str | None, str | None]:
    rating = None
    for selector in RATING_TEXT_SELECTORS:
        try:
            loc = scope.locator(selector)
            if await loc.count() == 0:
                continue
            node = loc.first
            value = await node.get_attribute("title")
            if not value:
                value = await node.get_attribute("aria-label")
            if not value:
                value = (await node.inner_text()).strip()
            if value:
                rating = value.strip()
                if "%" in rating:
                    import re

                    match = re.search(r"(\d{1,3})\s*%", rating)
                    if match:
                        percent = int(match.group(1))
                        rating = f"{max(0.0, min(5.0, percent / 20.0)):.1f}"
                break
        except Exception:
            continue
    review_count = await extract_text(scope, REVIEW_COUNT_SELECTORS)
    if not review_count:
        review_count = await extract_attr(scope, REVIEW_COUNT_SELECTORS, "title")
    if not review_count:
        review_count = await extract_attr(scope, REVIEW_COUNT_SELECTORS, "aria-label")
    return rating, review_count


async def detect_product_selector(page) -> str | None:
    for selector in PRODUCT_SELECTORS:
        try:
            await page.wait_for_selector(selector, timeout=15000)
            return selector
        except PlaywrightTimeoutError:
            continue
    return None


async def load_all_products(page, selector: str, max_rounds: int = 40) -> int:
    stable_rounds = 0
    last_count = 0
    for _ in range(max_rounds):
        cards = page.locator(selector)
        current_count = await cards.count()
        print(f"Currently found {current_count} products.")
        if current_count <= last_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
            last_count = current_count

        clicked = False
        for btn_selector in LOAD_MORE_SELECTORS:
            try:
                btn = page.locator(btn_selector)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click()
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

        await page.wait_for_timeout(int(random.uniform(1800, 3200)))
        if stable_rounds >= 2:
            break

    return await page.locator(selector).count()


async def scrape_products(page, category_name: str, subcategory_name: str, collection) -> int:
    selector = await detect_product_selector(page)
    if not selector:
        print(f"[WARN] No product selector found for {subcategory_name}.")
        return 0

    total_count = await load_all_products(page, selector=selector)
    print(f"Total {total_count} products found in {subcategory_name} after load.")
    scraped_products: list[dict] = []
    cards = page.locator(selector)
    for index in range(total_count):
        product = cards.nth(index)
        title = await extract_text(product, TITLE_SELECTORS)
        link = await extract_attr(product, TITLE_SELECTORS, "href")
        if not title or not link:
            continue
        price = await extract_text(product, PRICE_SELECTORS) or "Sold Out"
        if "sold out" in price.lower():
            continue
        rating, review_count = await extract_rating_and_review_count(product)
        images = await extract_image_gallery(product, IMAGE_SELECTORS, base_url=link)
        image = primary_image_from_gallery(images, base_url=link)

        scraped_products.append(
            {
                "category": category_name,
                "subcategory": subcategory_name,
                "title": title,
                "link": link,
                "price": price,
                "rating": rating,
                "review_count": review_count,
                "image": image,
                "images": images,
                "specifications": None,
                "source": "shophive",
                "last_scraped": now_stamp(),
            }
        )

    if not scraped_products:
        print(f"[INFO] No in-stock products captured for {subcategory_name}; cleanup skipped.")
        return 0

    ops = [UpdateOne({"link": row["link"]}, {"$set": row}, upsert=True) for row in scraped_products]
    collection.bulk_write(ops, ordered=False)
    links = [row["link"] for row in scraped_products]
    deleted = collection.delete_many({"source": "shophive", "subcategory": subcategory_name, "link": {"$nin": links}}).deleted_count
    print(
        f"Upserted {len(scraped_products)} products for {subcategory_name}; "
        f"removed {deleted} stale links."
    )
    return len(scraped_products)


async def scrape_shophive(category: str, subcategory: str, url: str, collection) -> None:
    print(f"\nScraping category: {category}, subcategory: {subcategory}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            no_products = page.locator("text=We can't find products matching the selection.")
            if await no_products.count() > 0 and await no_products.first.is_visible():
                print(f"No products found for {subcategory}.")
            else:
                await scrape_products(page, category, subcategory, collection)
        except PlaywrightTimeoutError:
            print(f"[WARN] Timeout while loading {url}")
        finally:
            await context.close()
            await browser.close()


async def run_cycle(collection, categories: dict) -> None:
    print("Starting a new scraping cycle.")
    for category, subcategories in categories.items():
        for subcategory, url in subcategories.items():
            await scrape_shophive(category, subcategory, url, collection)
    print("Completed a full scraping cycle.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Shophive Playwright scraper")
    parser.add_argument("--once", action="store_true", help="Run a single scrape cycle and exit")
    parser.add_argument("--categories-file", default="config/categories_shophive.yaml")
    parser.add_argument("--interval-seconds", type=int, default=10800)
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db-name", default=None)
    parser.add_argument("--collection-name", default=None)
    args = parser.parse_args()

    settings = Settings()
    mongo_uri = args.mongo_uri or settings.mongo_uri
    db_name = args.db_name or os.getenv("SHOPHIVE_SCRAPER_DB", "test")
    collection_name = args.collection_name or os.getenv("SHOPHIVE_SCRAPER_COLLECTION", "shophiveProducts")

    categories = load_categories(args.categories_file)
    collection = connect_to_db(mongo_uri, db_name, collection_name)

    if args.once:
        await run_cycle(collection, categories)
        return

    while True:
        await run_cycle(collection, categories)
        print(f"Sleeping for {args.interval_seconds} seconds before next cycle.")
        await asyncio.sleep(args.interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
