import requests
from bs4 import BeautifulSoup
import time
import random
import argparse
from pathlib import Path
from pymongo import MongoClient, errors
import yaml

from src.core.normalize import normalize_image_gallery, primary_image_from_gallery

# --- MongoDB Configuration ---
MONGO_URI = "mongodb://localhost:27017/"
DATABASE_NAME = "iShopping_Data" # Changed to be more descriptive
COLLECTION_NAME = "products"


# --- User-Agent Rotation List ---
# A diverse list of modern browser User-Agents for rotation
USER_AGENT_LIST = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.2478.67',
]

def get_random_user_agent():
    """Returns a randomly selected User-Agent string."""
    return random.choice(USER_AGENT_LIST)


# Connect to MongoDB and return the database and collection
def connect_to_db():
    try:
        # Increased serverSelectionTimeoutMS to help with connection stability
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        # The ismaster command is cheap and does not require auth.
        client.admin.command('ismaster')
        db = client[DATABASE_NAME]
        collection = db[COLLECTION_NAME]
        
        # Ensure a unique index on the product link for efficient upserts/updates
        collection.create_index("link", unique=True, name="link_unique_index")
        
        print("MongoDB connection successful! Index created/verified.")
        return db, collection
    except errors.ConnectionError as e:
        print(f"Failed to connect to MongoDB: {e}")
        return None, None
    except errors.ServerSelectionTimeoutError as e:
        print(f"MongoDB Server Selection Timeout Error: {e}")
        return None, None
    except Exception as e:
        print(f"An unexpected error occurred during DB connection: {e}")
        return None, None


def load_categories(path: str) -> dict:
    cfg_path = Path(path)
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    categories = data.get("categories", {})
    if not categories:
        raise ValueError(f"No categories found in {path}")
    return categories


def get_total_pages(soup):
    pagination = soup.find('ul', class_='pages-items')
    if pagination:
        # Find all list items that are NOT the "next" button
        page_items = [item for item in pagination.find_all('li', class_='item') if 'pages-item-next' not in item.get('class', [])]
        if page_items:
            # Check the last element in the filtered list
            last_item_text = page_items[-1].text.strip()
            if last_item_text.isdigit():
                return int(last_item_text)
            
            # Fallback to check the text inside the last link
            last_link = page_items[-1].find('a')
            if last_link and last_link.text.strip().isdigit():
                return int(last_link.text.strip())
    
    # If no pagination is found, assume only one page
    return 1


# Initialize a session for connection pooling, but headers will be updated per request
session = requests.Session()
# Set static default headers (Accept, Accept-Language, etc.)
session.headers.update({
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
    "Connection": "keep-alive"
})

def retry_request(url, max_retries=5, delay=3):
    for attempt in range(max_retries):
        try:
            # 1. GENERATE A RANDOM USER-AGENT FOR THIS REQUEST
            user_agent = get_random_user_agent()
            
            # 2. Prepare the custom headers for this attempt
            current_headers = session.headers.copy()
            current_headers['User-Agent'] = user_agent
            
            # Dynamic Referer: Pretend to have come from the base page or a previous page
            base_url = '/'.join(url.split('/')[:3])
            current_headers['Referer'] = url.rsplit('?', 1)[0] if '?' in url else base_url
            
            print(f"  > Attempt {attempt + 1}: Using UA: {user_agent[:50]}...")

            # 3. Make the request
            resp = session.get(url, headers=current_headers, timeout=15)
            resp.raise_for_status()
            
            # Check for a content-length that indicates a block page (if possible)
            if resp.status_code == 200 and ("Forbidden" in resp.text or "Access Denied" in resp.text or "bot" in resp.text.lower()):
                raise requests.exceptions.RequestException("Suspected Anti-Scraping Block Page/403")
            
            return resp
            
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else 'Unknown'
            print(f"  > Error fetching {url}: HTTP Error {status_code}. Retrying in {delay}s...")
            
        except requests.exceptions.RequestException as e:
            # Catch the custom exception or other request errors (e.g., Timeout, ConnectionError)
            print(f"  > Error fetching {url}: {type(e).__name__} - {e}. Retrying in {delay}s...")
        
        except Exception as e:
            print(f"  > Unexpected Error: {type(e).__name__} - {e}. Retrying in {delay}s...")
        
        # Delay before retrying
        if attempt < max_retries - 1:
            time.sleep(delay)
            delay = min(delay * 1.5, 30) # Exponential backoff with a max delay of 30 seconds
            
    print(f"Max retries reached for {url}. Skipping this URL.")
    return None


def scrape_subcategory(category_name, subcategory_name, subcategory_url, collection):
    print(f"\n--- Scraping category: {category_name}, subcategory: {subcategory_name} ---")
    
    # 1. Get existing product links from DB to track updates/deletions
    existing_products_cursor = collection.find({"subcategory": subcategory_name}, {"link": 1})
    existing_product_links = {prod['link'] for prod in existing_products_cursor}
    scraped_product_links = set() # Links scraped in this run

    # 2. Scrape the first page to determine total pages
    response = retry_request(subcategory_url)
    if response is None:
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    total_pages = get_total_pages(soup)
    print(f"Total pages for {subcategory_name}: {total_pages}")
    
    # List of page responses to process (to avoid re-fetching page 1)
    page_responses = {1: response}

    # 3. Iterate through all pages
    for page in range(1, total_pages + 1):
        page_url = f"{subcategory_url}?p={page}"
        print(f"Scraping page {page}/{total_pages} of {subcategory_name} at {page_url}")
        
        # Determine the response object to use
        page_response = page_responses.get(page)
        if page_response is None:
            page_response = retry_request(page_url)
            if page_response is None:
                continue

        page_soup = BeautifulSoup(page_response.text, 'html.parser')
        
        # Target the main product list container
        product_list = page_soup.find('ol', class_='product-items')
        
        if product_list is None:
            print(f"Warning: No product list found on page {page}. Check selectors or page structure.")
            continue
            
        # Select product links
        products = product_list.find_all('a', class_='product-item-link')

        # 4. Extract data from each product item
        for product in products:
            try:
                title = product.text.strip()
                link = product['href']
                
                # Navigate up to the main product-info container for related data
                product_item = product.find_parent('div', class_='product-item-info')
                
                # Find price (usually in the price-box)
                price_tag = product_item.find('span', class_='price') if product_item else None
                price = price_tag.text.strip() if price_tag else 'N/A'
                rating = extract_rating_text(product_item)
                review_count = extract_review_count_text(product_item)
                
                # Find image (look up for the picture tag, then img src)
                images = extract_product_images(product_item, page_url=page_url)
                img_tag = primary_image_from_gallery(images, base_url=link)

                # Add link to scraped links for later comparison
                scraped_product_links.add(link)

                # Prepare product data
                product_data = {
                    "category": category_name,
                    "subcategory": subcategory_name,
                    "title": title,
                    "link": link,
                    "price": price,
                    "rating": rating,
                    "review_count": review_count,
                    "image": img_tag,
                    "images": images,
                    "specifications": "Needs dedicated product page scrape", # Placeholder for product details
                    "source": "iShopping",
                    "last_scraped": time.strftime("%Y-%m-%d %H:%M:%S")
                }

                # 5. Update or insert the product in MongoDB (Upsert logic)
                if link in existing_product_links:
                    # Update existing product
                    collection.update_one({"link": link}, {"$set": product_data})
                else:
                    # Insert new product
                    collection.insert_one(product_data)
                    print(f"    [NEW] Inserted: {title[:50]}...")
            
            except Exception as e:
                print(f"    [ERROR] Failed to process product on page {page}. Error: {e}")
                continue

        # Add a polite, randomized delay between pages (2 to 5 seconds)
        time.sleep(random.uniform(2, 5))

    # 6. Clean-up: Remove products no longer present on the website
    products_to_remove = existing_product_links - scraped_product_links
    if products_to_remove:
        print(f"\n[CLEANUP] Removing {len(products_to_remove)} old/deleted products for {subcategory_name}...")
        collection.delete_many({"link": {"$in": list(products_to_remove)}})
        
    print(f"--- Subcategory {subcategory_name} done! Total active products: {collection.count_documents({'subcategory': subcategory_name})} ---")


def scrape_and_save_data(collection, categories: dict):
    """Main function to loop through all categories and subcategories."""
    for category_name, subcategories in categories.items():
        print(f"\n--- Starting Category: {category_name} ({len(subcategories)} subcategories) ---")
        for subcategory_name, subcategory_url in subcategories.items():
            scrape_subcategory(category_name, subcategory_name, subcategory_url, collection)


def extract_product_images(product_item, *, page_url: str) -> list[str]:
    if product_item is None:
        return []
    raw_values: list[str] = []
    for node in product_item.select("picture source, picture img, img"):
        for attr in ("src", "data-src", "data-lazy-src", "srcset", "data-srcset"):
            value = node.get(attr)
            if value and str(value).strip():
                raw_values.append(str(value).strip())
    images = normalize_image_gallery(raw_values, base_url=page_url)
    if images:
        return images
    img_node = product_item.find("img")
    if not img_node:
        return []
    fallback = img_node.get("src") or img_node.get("data-src")
    return normalize_image_gallery(fallback, base_url=page_url)


def extract_rating_text(product_item) -> str | None:
    if product_item is None:
        return None
    node = product_item.select_one(".rating-summary .rating-result, .product-reviews-summary .rating-result")
    if node is None:
        return None
    value = node.get("title") or node.get("aria-label") or node.get_text(" ", strip=True)
    text = " ".join(str(value or "").split()).strip()
    return text or None


def extract_review_count_text(product_item) -> str | None:
    if product_item is None:
        return None
    node = product_item.select_one(".reviews-actions .action.view, .product-reviews-summary .reviews-actions")
    if node is None:
        return None
    text = " ".join((node.get_text(" ", strip=True) or "").split()).strip()
    return text or None


def run_cycle(collection, categories: dict):
    print("\n=============================================")
    print("STARTING NEW iShopping SCRAPING CYCLE")
    print("=============================================")
    scrape_and_save_data(collection, categories)
    print("\n=============================================")
    print("SCRAPING CYCLE COMPLETED.")
    print("=============================================")


def main():
    parser = argparse.ArgumentParser(description="iShopping requests+bs4 scraper")
    parser.add_argument("--once", action="store_true", help="Run a single scrape cycle and exit")
    parser.add_argument("--categories-file", default="config/categories_ishopping.yaml")
    args = parser.parse_args()
    categories = load_categories(args.categories_file)

    # Connect to MongoDB
    db, collection = connect_to_db()

    if collection is not None:
        if args.once:
            run_cycle(collection, categories)
            return
        while True:
            run_cycle(collection, categories)
            print("Waiting for 1 hour (3600 seconds) before the next cycle...")
            time.sleep(3600)  # Wait for 1 hour before the next run

if __name__ == "__main__":
    main()
