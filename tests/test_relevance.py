from src.core.relevance import filter_relevant_results


def test_filter_relevant_results_prefers_audio_products_for_earbud_query():
    rows = [
        {
            "title": "i20 Ultra Max Suit with Earphones 7 in 1 set with earbuds",
            "link": "https://daraz.pk/watch-bundle",
            "source": "daraz",
        },
        {
            "title": "M25 Gaming Wireless Earbuds",
            "link": "https://priceoye.pk/m25-earbuds",
            "source": "priceoye",
        },
    ]

    out = filter_relevant_results("search for gaming earbuds", rows)
    assert len(out) == 1
    assert out[0]["title"] == "M25 Gaming Wireless Earbuds"


def test_filter_relevant_results_drops_generic_listing_pages_for_audio_query():
    rows = [
        {
            "title": "Price List of Best Gaming Earbuds in Pakistan",
            "link": "https://priceoye.pk/wireless-earbuds/pricelist/best-gaming-earbuds",
            "source": "priceoye",
        },
        {
            "title": "Ronin R-7015 Gaming Earbuds",
            "link": "https://ronin.pk/collections/gaming-buds/products/r-7015",
            "source": "ronin",
        },
    ]

    out = filter_relevant_results("gaming earbuds", rows)
    assert len(out) == 1
    assert out[0]["title"] == "Ronin R-7015 Gaming Earbuds"


def test_filter_relevant_results_prefers_watch_products_for_watch_query():
    rows = [
        {
            "title": "M25 Gaming Wireless Earbuds",
            "link": "https://priceoye.pk/m25-earbuds",
            "source": "priceoye",
        },
        {
            "title": "Samsung Galaxy Watch 6 Classic",
            "link": "https://example.com/watch6",
            "source": "samsung",
        },
    ]

    out = filter_relevant_results("samsung watch", rows)
    assert len(out) == 1
    assert out[0]["title"] == "Samsung Galaxy Watch 6 Classic"
