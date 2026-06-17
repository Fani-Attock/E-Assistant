from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from pymongo import MongoClient
import argparse
from pathlib import Path
import time
import random
import yaml
import re
import json

from src.core.normalize import normalize_image_gallery, primary_image_from_gallery

# ------------------ Product Scraper Agent ------------------
class ScraperAgent:
    def __init__(self, mongo_uri="mongodb://localhost:27017/", db_name="FYP_Products", collection_name="products"):
        self.client = MongoClient(mongo_uri)
        self.db = self.client[db_name]
        self.collection = self.db[collection_name]
        # Ensure unique index on link
        self.collection.create_index("link", unique=True)
        self.debug_dir = Path("artifacts/scraper_debug/ishopping")
        self.debug_dir.mkdir(parents=True, exist_ok=True)

    def scrape_ishopping(self, categories: dict, max_pages_per_subcategory: int = 0):
        """
        Scrapes iShopping.pk products for all categories/subcategories provided.
        categories = {
            "Mobiles": {"Apple": "url", "Samsung": "url"},
            "Laptops": {"Dell": "url", "HP": "url"}
        }
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                user_agent=f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.randint(100,120)}.0.0.0 Safari/537.36'
            )
            page = context.new_page()
            page.set_extra_http_headers({"Referer": "https://www.google.com/"})

            for cat_name, subcats in categories.items():
                for subcat_name, url in subcats.items():
                    try:
                        self._scrape_subcategory(page, cat_name, subcat_name, url, max_pages_per_subcategory)
                    except Exception as e:
                        # Never let a single blocked subcategory crash the whole scraper run.
                        print(f"[WARN] Skipping subcategory {subcat_name} due to error: {e}")

            browser.close()

    def _scrape_subcategory(self, page, category_name, subcategory_name, url, max_pages):
        print(f"\n--- Scraping {subcategory_name} under {category_name} ---")
        time.sleep(random.uniform(1, 3))

        PRODUCT_SELECTORS = [
            ".product-item",
            "li.product-item",
            "ol.product-items li",
            ".products.list.items .item.product",
        ]
        TITLE_SELECTOR = '.product-item-details a.product-item-link'
        PRICE_SELECTOR = '.price-box .price'
        IMAGE_SELECTOR = '.product-item-info .product-image-container img'
        RATING_SELECTOR = '.rating-summary .rating-result, .product-reviews-summary .rating-result'
        REVIEW_COUNT_SELECTOR = '.reviews-actions .action.view, .product-reviews-summary .reviews-actions'
        NEXT_SELECTOR = 'ul.pages-items li.item-next a.action.next'

        scraped_links = set()
        current_page = 1
        had_product_page = False

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_load_state("networkidle", timeout=20000)
        except PlaywrightTimeoutError:
            print(f"[WARN] Initial page load timeout for {subcategory_name}; skipping.")
            return

        while True:
            if max_pages and current_page > max_pages:
                break
            print(f"Scraping page {current_page}...")
            active_selector = None
            for selector in PRODUCT_SELECTORS:
                try:
                    page.wait_for_selector(selector, timeout=10000)
                    active_selector = selector
                    had_product_page = True
                    break
                except PlaywrightTimeoutError:
                    continue
            if not active_selector:
                title_text = (page.title() or "").lower()
                if any(x in title_text for x in ("forbidden", "access denied", "captcha")):
                    print(f"[WARN] Access blocked while scraping {subcategory_name}; stopping this subcategory.")
                else:
                    print(f"[WARN] No product selector found for {subcategory_name} page {current_page}; stopping.")
                fallback_items = self._extract_products_from_jsonld(page)
                if fallback_items:
                    had_product_page = True
                    self._save_fallback_products(fallback_items, category_name, subcategory_name, scraped_links)
                    print(f"[INFO] Saved {len(fallback_items)} products from JSON-LD fallback for {subcategory_name}.")
                self._dump_debug_artifacts(page, category_name, subcategory_name, current_page)
                break
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(3000)

            products = page.query_selector_all(active_selector)
            if not products:
                print("No products found.")
                break

            for prod in products:
                try:
                    link_element = prod.query_selector(TITLE_SELECTOR)
                    link = link_element.get_attribute('href') if link_element else None
                    title = link_element.text_content().strip() if link_element else "No Title"

                    if not link or link in scraped_links:
                        continue
                    scraped_links.add(link)

                    price_el = prod.query_selector(PRICE_SELECTOR)
                    price = price_el.text_content().strip() if price_el else "No Price"
                    rating = self._extract_rating_text(prod, RATING_SELECTOR)
                    review_count = self._extract_text(prod, REVIEW_COUNT_SELECTOR)

                    images = self._extract_product_images(prod, IMAGE_SELECTOR, base_url=link or url)
                    img_src = primary_image_from_gallery(images, base_url=link or url)

                    product_info = {
                        "category": category_name,
                        "subcategory": subcategory_name,
                        "title": title,
                        "link": link,
                        "price": price,
                        "rating": rating,
                        "review_count": review_count,
                        "image": img_src,
                        "images": images,
                        "source": "iShopping.pk",
                        "last_scraped": time.strftime("%Y-%m-%d %H:%M:%S")
                    }

                    # Upsert into MongoDB
                    self.collection.update_one({"link": link}, {"$set": product_info}, upsert=True)
                    print(f"Saved: {title} → {price}")

                except Exception as e:
                    print(f"[ERROR] Product scrape failed: {e}")

            # Pagination
            next_btn = page.query_selector(NEXT_SELECTOR)
            if next_btn:
                next_btn.click()
                current_page += 1
                page.wait_for_load_state("networkidle", timeout=15000)
                time.sleep(random.uniform(2, 5))
            else:
                break

        # Cleanup only when at least one product page was successfully read.
        if had_product_page:
            all_links_db = {item["link"] for item in self.collection.find({"subcategory": subcategory_name}, {"link": 1})}
            removed_links = all_links_db - scraped_links
            if removed_links:
                self.collection.delete_many({"link": {"$in": list(removed_links)}})
                print(f"Removed {len(removed_links)} old/deleted products.")
        else:
            print(f"[INFO] Cleanup skipped for {subcategory_name} due to blocked/empty scrape.")
        print(f"Finished scraping {subcategory_name}.")

    def _dump_debug_artifacts(self, page, category_name: str, subcategory_name: str, current_page: int) -> None:
        safe_cat = re.sub(r"[^a-zA-Z0-9_-]+", "_", category_name)[:40]
        safe_sub = re.sub(r"[^a-zA-Z0-9_-]+", "_", subcategory_name)[:60]
        ts = time.strftime("%Y%m%d_%H%M%S")
        base = f"{safe_cat}__{safe_sub}__p{current_page}__{ts}"
        html_path = self.debug_dir / f"{base}.html"
        png_path = self.debug_dir / f"{base}.png"
        try:
            html_path.write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(png_path), full_page=True)
            print(f"[DEBUG] Saved debug artifacts: {html_path} and {png_path}")
        except Exception as e:
            print(f"[DEBUG] Failed to save debug artifacts: {e}")

    def _save_fallback_products(self, items: list[dict], category_name: str, subcategory_name: str, scraped_links: set) -> None:
        for item in items:
            link = item.get("link")
            title = item.get("title", "No Title")
            if not link or link in scraped_links:
                continue
            scraped_links.add(link)
            images = normalize_image_gallery(item.get("images") or item.get("image"), base_url=link)
            product_info = {
                "category": category_name,
                "subcategory": subcategory_name,
                "title": title,
                "link": link,
                "price": item.get("price", "No Price"),
                "rating": item.get("rating"),
                "review_count": item.get("review_count"),
                "image": primary_image_from_gallery(images, base_url=link),
                "images": images,
                "source": "iShopping.pk",
                "last_scraped": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.collection.update_one({"link": link}, {"$set": product_info}, upsert=True)
            print(f"Saved(JSON-LD): {title} -> {product_info['price']}")

    def _extract_products_from_jsonld(self, page) -> list[dict]:
        out: list[dict] = []
        try:
            scripts = page.query_selector_all("script[type='application/ld+json']")
            for script in scripts:
                raw = (script.text_content() or "").strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                for obj in self._iter_json_nodes(data):
                    if not isinstance(obj, dict) or obj.get("@type") != "Product":
                        continue
                    link = obj.get("url")
                    if not link:
                        continue
                    price = "No Price"
                    offers = obj.get("offers")
                    if isinstance(offers, dict) and offers.get("price") is not None:
                        price = f"PKR {offers.get('price')}"
                    elif isinstance(offers, list) and offers and isinstance(offers[0], dict) and offers[0].get("price") is not None:
                        price = f"PKR {offers[0].get('price')}"
                    images = normalize_image_gallery(obj.get("image"), base_url=link)
                    out.append(
                        {
                            "title": obj.get("name") or "No Title",
                            "link": link,
                            "price": price,
                            "rating": (obj.get("aggregateRating") or {}).get("ratingValue") if isinstance(obj.get("aggregateRating"), dict) else None,
                            "review_count": (obj.get("aggregateRating") or {}).get("reviewCount") if isinstance(obj.get("aggregateRating"), dict) else None,
                            "image": primary_image_from_gallery(images, base_url=link),
                            "images": images,
                        }
                    )
        except Exception as e:
            print(f"[DEBUG] JSON-LD fallback extraction failed: {e}")
        return out

    def _iter_json_nodes(self, data):
        if isinstance(data, dict):
            yield data
            for value in data.values():
                if isinstance(value, (dict, list)):
                    yield from self._iter_json_nodes(value)
        elif isinstance(data, list):
            for item in data:
                yield from self._iter_json_nodes(item)

    def _extract_product_images(self, product, image_selector: str, *, base_url: str | None) -> list[str]:
        raw_values: list[str] = []
        try:
            img_nodes = product.query_selector_all(image_selector)
        except Exception:
            img_nodes = []
        for node in img_nodes[:6]:
            for attr in ("src", "data-src", "data-lazy-src", "srcset", "data-srcset"):
                try:
                    value = node.get_attribute(attr)
                except Exception:
                    value = None
                if value and value.strip():
                    raw_values.append(value.strip())
        try:
            picture_nodes = product.query_selector_all("picture source")
        except Exception:
            picture_nodes = []
        for node in picture_nodes[:6]:
            for attr in ("srcset", "data-srcset"):
                try:
                    value = node.get_attribute(attr)
                except Exception:
                    value = None
                if value and value.strip():
                    raw_values.append(value.strip())
        images = normalize_image_gallery(raw_values, base_url=base_url)
        if images:
            return images
        try:
            img_el = product.query_selector(image_selector)
        except Exception:
            img_el = None
        fallback = None
        if img_el:
            for attr in ("src", "data-src"):
                try:
                    candidate = img_el.get_attribute(attr)
                except Exception:
                    candidate = None
                if candidate and candidate.strip():
                    fallback = candidate.strip()
                    break
        return normalize_image_gallery(fallback, base_url=base_url)

    def _extract_text(self, scope, selector: str) -> str | None:
        try:
            node = scope.query_selector(selector)
        except Exception:
            node = None
        if not node:
            return None
        try:
            text = (node.text_content() or "").strip()
        except Exception:
            text = ""
        return text or None

    def _extract_rating_text(self, scope, selector: str) -> str | None:
        try:
            node = scope.query_selector(selector)
        except Exception:
            node = None
        if not node:
            return None
        for attr in ("title", "aria-label"):
            try:
                value = node.get_attribute(attr)
            except Exception:
                value = None
            if value and value.strip():
                return value.strip()
        return self._extract_text(scope, selector)


def load_categories(path: str) -> dict:
    cfg_path = Path(path)
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    categories = data.get("categories", {})
    if not categories:
        raise ValueError(f"No categories found in {path}")
    return categories


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="iShopping Playwright sync scraper")
    parser.add_argument("--categories-file", default="config/categories_ishopping.yaml")
    parser.add_argument("--max-pages", type=int, default=0, help="0 means scrape all pages")
    args = parser.parse_args()

    categories = load_categories(args.categories_file)
    agent = ScraperAgent()
    agent.scrape_ishopping(categories, max_pages_per_subcategory=args.max_pages)
