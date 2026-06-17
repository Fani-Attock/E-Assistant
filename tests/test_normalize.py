from jobs.normalize_offers import normalize_one
from src.core.normalize import (
    extract_brand,
    extract_storage_gb,
    normalize_image_gallery,
    normalize_price_to_pkr,
    normalize_search_query,
    normalize_text,
    parse_srcset_urls,
    primary_image_from_gallery,
)
from src.core.query_parser import parse_query


def test_normalize_price_to_pkr():
    assert normalize_price_to_pkr("Rs. 123,456") == 123456.0
    assert normalize_price_to_pkr("PKR 89999") == 89999.0
    assert normalize_price_to_pkr(None) is None


def test_normalize_text():
    assert normalize_text("  iPhone   14  Pro ") == "iphone 14 pro"


def test_extract_brand_storage():
    assert extract_brand("Apple iPhone 14 Pro 256GB PTA") == "apple"
    assert extract_storage_gb("Apple iPhone 14 Pro 256GB PTA") == 256


def test_parse_query():
    parsed = parse_query("Samsung phone under 150000 256GB")
    assert parsed.brand == "samsung"
    assert parsed.max_price_pkr == 150000.0
    assert parsed.storage_gb == 256


def test_normalize_search_query_fixes_common_typos_and_filler():
    assert normalize_search_query("fin gamming earbuds in pakistan") == "gaming earbuds"


def test_parse_query_uses_typo_tolerant_cleaned_query():
    parsed = parse_query("fin gamming earbuds in pakistan")
    assert parsed.cleaned_query == "gaming earbuds"


def test_parse_srcset_urls_extracts_candidates():
    assert parse_srcset_urls("https://cdn.test/a.jpg 1x, https://cdn.test/b.jpg 2x") == [
        "https://cdn.test/a.jpg",
        "https://cdn.test/b.jpg",
    ]


def test_normalize_image_gallery_dedupes_and_filters_placeholders():
    values = [
        "https://cdn.test/product-1.jpg",
        "https://cdn.test/product-1.jpg",
        "https://via.placeholder.com/300",
        "https://cdn.test/product-2.jpg 2x, https://cdn.test/product-3.jpg 3x",
    ]
    assert normalize_image_gallery(values) == [
        "https://cdn.test/product-1.jpg",
        "https://cdn.test/product-2.jpg",
        "https://cdn.test/product-3.jpg",
    ]


def test_primary_image_from_gallery_uses_first_valid_item():
    values = ["/media/item-2.jpg", "/media/item-3.jpg"]
    assert primary_image_from_gallery(values, base_url="https://shop.test/products/item") == "https://shop.test/media/item-2.jpg"


def test_normalize_one_preserves_gallery_and_primary_image():
    out = normalize_one(
        {
            "offer_id": "offer_123",
            "source": "daraz",
            "title": "Gaming Earbuds X",
            "link": "https://example.test/earbuds-x",
            "image": "https://via.placeholder.com/300",
            "images": [
                "https://cdn.example.test/earbuds-main.jpg",
                "https://cdn.example.test/earbuds-side.jpg",
            ],
            "raw_price": "PKR 4,999",
            "last_scraped": "2026-05-01 10:00:00",
        }
    )
    assert out is not None
    assert out["image"] == "https://cdn.example.test/earbuds-main.jpg"
    assert out["images"] == [
        "https://cdn.example.test/earbuds-main.jpg",
        "https://cdn.example.test/earbuds-side.jpg",
    ]


def test_normalize_one_parses_scraped_rating_and_review_count():
    out = normalize_one(
        {
            "offer_id": "offer_456",
            "source": "daraz",
            "title": "Wireless Earbuds Y",
            "link": "https://example.test/earbuds-y",
            "raw_price": "PKR 7,999",
            "rating": "4.7 out of 5",
            "review_count": "(182)",
            "last_scraped": "2026-05-03 10:00:00",
        }
    )
    assert out is not None
    assert out["rating"] == 4.7
    assert out["review_count"] == 182
