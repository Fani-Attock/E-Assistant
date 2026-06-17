from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import yaml
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.normalize import normalize_image_gallery, primary_image_from_gallery
from src.core.settings import Settings

PRODUCT_SELECTORS = [
    "div[data-qa-locator='product-item']",
    "div.Bm3ON",
    "div.gridItem--Yd0sa",
]
TITLE_SELECTORS = [
    "a[title]",
    ".RfADt a",
    "a",
]
PRICE_SELECTORS = [
    "span[data-qa-locator='product-price']",
    ".aBrP0 span",
    ".ooOxS",
]
RATING_SELECTORS = [
    "[data-qa-locator='product-rating']",
    "span._9-ogB",
    "span[class*='rating']",
]
REVIEW_COUNT_SELECTORS = [
    "span.qzqFw",
    "[data-qa-locator='product-rating-count']",
    "span[class*='review']",
]
IMAGE_SELECTORS = [
    "img",
]
IMAGE_ATTRS = ("src", "data-src", "data-lazy-src", "srcset", "data-srcset")
NEXT_PAGE_SELECTORS = [
    "li.ant-pagination-next:not(.ant-pagination-disabled) a",
    "a[title='Next Page']",
]
STAR_ICON_SELECTOR = "div.mdmmT i._9-ogB, div.mdmmT i[class*='_9-ogB']"
DARAZ_STAR_CLASS_SCORES = {
    "Dy1nx": 1.0,
    "K8PID": 0.8,
    "i6t3-": 0.7,
    "B4Foa": 0.5,
    "TZlP8": 0.3,
    "yWGJ-": 0.2,
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_categories(path: str) -> dict:
    cfg_path = Path(path)
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    categories = data.get("categories", {})
    if not categories:
        raise ValueError(f"No categories found in {path}")
    return categories


def normalize_link(raw_link: str) -> str:
    value = raw_link.strip()
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return urljoin("https://www.daraz.pk", value)
    return value


def build_page_url(base_url: str, page_num: int) -> str:
    if page_num <= 1:
        return base_url
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query)
    query["page"] = [str(page_num)]
    updated = parsed._replace(query=urlencode(query, doseq=True))
    return urlunparse(updated)


async def extract_text(scope, selectors: list[str]) -> str | None:
    for selector in selectors:
        try:
            loc = scope.locator(selector)
            if await loc.count() == 0:
                continue
            text = (await loc.first.inner_text()).strip()
            if text:
                return text
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


def daraz_star_rating_from_classes(classes: list[str]) -> str | None:
    if not classes:
        return None
    total = 0.0
    matched = False
    for value in classes[:5]:
        tokens = str(value or "").split()
        for token in tokens:
            if token in DARAZ_STAR_CLASS_SCORES:
                total += DARAZ_STAR_CLASS_SCORES[token]
                matched = True
                break
    if not matched:
        return None
    return f"{max(0.0, min(5.0, total)):.1f}"


async def extract_star_rating(scope) -> str | None:
    try:
        icons = scope.locator(STAR_ICON_SELECTOR)
        count = min(await icons.count(), 5)
    except Exception:
        return None
    classes: list[str] = []
    for idx in range(count):
        try:
            class_name = await icons.nth(idx).get_attribute("class")
        except Exception:
            class_name = None
        if class_name:
            classes.append(class_name)
    return daraz_star_rating_from_classes(classes)


async def extract_rating_review_text(scope) -> tuple[str | None, str | None]:
    rating = await extract_text(scope, RATING_SELECTORS)
    if not rating:
        rating = await extract_attr(scope, RATING_SELECTORS, "title")
    if not rating:
        rating = await extract_attr(scope, RATING_SELECTORS, "aria-label")
    if not rating:
        rating = await extract_star_rating(scope)
    if rating and "%" in rating:
        import re

        match = re.search(r"(\d{1,3})\s*%", rating)
        if match:
            percent = int(match.group(1))
            rating = f"{max(0.0, min(5.0, percent / 20.0)):.1f}"
    review_count = await extract_text(scope, REVIEW_COUNT_SELECTORS)
    if not review_count:
        review_count = await extract_attr(scope, REVIEW_COUNT_SELECTORS, "title")
    if not review_count:
        review_count = await extract_attr(scope, REVIEW_COUNT_SELECTORS, "aria-label")
    return rating, review_count


async def wait_for_product_selector(page) -> str | None:
    for selector in PRODUCT_SELECTORS:
        try:
            await page.wait_for_selector(selector, timeout=12000)
            return selector
        except PlaywrightTimeoutError:
            continue
    return None


async def has_next_page(page) -> bool:
    for selector in NEXT_PAGE_SELECTORS:
        try:
            loc = page.locator(selector)
            if await loc.count() > 0 and await loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


class DarazScraper:
    def __init__(self, mongo_uri: str, db_name: str, collection_name: str):
        self.client = MongoClient(mongo_uri)
        self.db = self.client[db_name]
        self.collection = self.db[collection_name]
        self.collection.create_index("link", unique=True)

    async def scrape_cycle(self, categories: dict, max_pages: int) -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=random.choice(USER_AGENTS))
            page = await context.new_page()
            page.set_default_timeout(30000)
            for category, subcategories in categories.items():
                for subcategory, url in subcategories.items():
                    await self._scrape_subcategory(page, category, subcategory, url, max_pages)
            await context.close()
            await browser.close()

    async def _scrape_subcategory(self, page, category: str, subcategory: str, url: str, max_pages: int) -> None:
        print(f"\n--- Scraping {subcategory} under {category} ---")
        scraped_links: set[str] = set()
        had_product_page = False
        page_num = 1
        while True:
            if max_pages > 0 and page_num > max_pages:
                break
            target = build_page_url(url, page_num)
            print(f"Page {page_num}: {target}")
            try:
                await page.goto(target, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_load_state("networkidle", timeout=15000)
            except PlaywrightTimeoutError:
                print(f"[WARN] Timeout while loading {target}")
                break

            selector = await wait_for_product_selector(page)
            if not selector:
                page_title = (await page.title()).lower()
                if "access denied" in page_title or "forbidden" in page_title:
                    print(f"[WARN] Daraz blocked access for {subcategory}.")
                else:
                    print(f"[WARN] No product selector found for {subcategory} page {page_num}.")
                break

            cards = page.locator(selector)
            count = await cards.count()
            if count == 0:
                break
            had_product_page = True
            print(f"Found {count} cards on page {page_num}.")

            for idx in range(count):
                card = cards.nth(idx)
                raw_link = await extract_attr(card, TITLE_SELECTORS, "href")
                if not raw_link:
                    continue
                link = normalize_link(raw_link)
                if not link or link in scraped_links:
                    continue
                scraped_links.add(link)
                title = await extract_attr(card, ["a[title]"], "title")
                if not title:
                    title = await extract_text(card, TITLE_SELECTORS)
                if not title:
                    continue
                price = await extract_text(card, PRICE_SELECTORS) or "No Price"
                rating, review_count = await extract_rating_review_text(card)
                images = await extract_image_gallery(card, IMAGE_SELECTORS, base_url=link)
                image = primary_image_from_gallery(images, base_url=link)

                doc = {
                    "category": category,
                    "subcategory": subcategory,
                    "title": title.strip(),
                    "link": link,
                    "price": price.strip(),
                    "rating": rating,
                    "review_count": review_count,
                    "image": image,
                    "images": images,
                    "source": "daraz",
                    "last_scraped": now_stamp(),
                }
                self.collection.update_one({"link": link}, {"$set": doc}, upsert=True)

            if not await has_next_page(page):
                break
            page_num += 1
            await asyncio.sleep(random.uniform(1.0, 2.2))

        if had_product_page and scraped_links:
            existing_links = {
                row["link"]
                for row in self.collection.find(
                    {"source": "daraz", "category": category, "subcategory": subcategory},
                    {"_id": 0, "link": 1},
                )
            }
            stale = list(existing_links - scraped_links)
            if stale:
                result = self.collection.delete_many({"source": "daraz", "link": {"$in": stale}})
                print(f"Removed {result.deleted_count} stale products in {subcategory}.")
        elif not had_product_page:
            print(f"[INFO] Cleanup skipped for {subcategory} due to blocked/empty scrape.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Daraz Playwright scraper")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--categories-file", default="config/categories_daraz.yaml")
    parser.add_argument("--max-pages", type=int, default=3, help="Max pages per subcategory (0 = until next button ends)")
    parser.add_argument("--interval-seconds", type=int, default=10800)
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db-name", default=None)
    parser.add_argument("--collection-name", default=None)
    args = parser.parse_args()

    settings = Settings()
    mongo_uri = args.mongo_uri or settings.mongo_uri
    db_name = args.db_name or os.getenv("DARAZ_SCRAPER_DB", "Daraz_Data")
    collection_name = args.collection_name or os.getenv("DARAZ_SCRAPER_COLLECTION", "products")
    categories = load_categories(args.categories_file)
    scraper = DarazScraper(mongo_uri=mongo_uri, db_name=db_name, collection_name=collection_name)

    if args.once:
        await scraper.scrape_cycle(categories=categories, max_pages=args.max_pages)
        return

    while True:
        await scraper.scrape_cycle(categories=categories, max_pages=args.max_pages)
        print(f"Sleeping for {args.interval_seconds} seconds before next cycle.")
        await asyncio.sleep(args.interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
