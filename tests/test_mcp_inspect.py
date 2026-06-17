from src.mcp.server import MCPToolServer


def test_inspect_product_page_extracts_jsonld_signals(monkeypatch):
    html = """
    <html>
      <head>
        <title>Test Product</title>
        <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "Sample Perfume",
            "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.6", "reviewCount": "137"},
            "offers": {"@type": "Offer", "price": "2499", "availability": "https://schema.org/InStock"},
            "review": [{"reviewBody": "Very long lasting fragrance with great projection."}]
          }
        </script>
      </head>
      <body>In stock now</body>
    </html>
    """
    server = MCPToolServer.__new__(MCPToolServer)
    monkeypatch.setattr(server, "_fetch_html_safe", lambda link, headers: ("https://example.com/product", html))

    out = server._tool_inspect_product_page({"link": "https://example.com/product", "source": "example"}, {})
    assert out["rating"] == 4.6
    assert out["review_count"] >= 1
    assert out["price_pkr"] == 2499.0
    assert out["has_review_signals"] is True


def test_inspect_product_page_collects_image_gallery(monkeypatch):
    html = """
    <html>
      <head>
        <title>Image Gallery Product</title>
        <meta property="og:image" content="https://cdn.example.com/main.jpg" />
      </head>
      <body>
        <picture>
          <source srcset="https://cdn.example.com/alt-1.jpg 1x, https://cdn.example.com/alt-2.jpg 2x" />
          <img src="https://cdn.example.com/main.jpg" data-src="https://cdn.example.com/lazy.jpg" />
        </picture>
      </body>
    </html>
    """
    server = MCPToolServer.__new__(MCPToolServer)
    monkeypatch.setattr(server, "_fetch_html_safe", lambda link, headers: ("https://example.com/product", html))

    out = server._tool_inspect_product_page({"link": "https://example.com/product", "source": "example"}, {})
    assert out["image"] == "https://cdn.example.com/main.jpg"
    assert out["images"][0] == "https://cdn.example.com/main.jpg"
    assert "https://cdn.example.com/alt-1.jpg" in out["images"]
    assert "https://cdn.example.com/alt-2.jpg" in out["images"]


def test_inspect_product_page_extracts_delivery_and_warranty_signals(monkeypatch):
    html = """
    <html>
      <head><title>Power Station</title></head>
      <body>
        <div>Delivery charges Rs. 250 nationwide. Delivery in 3-5 working days.</div>
        <div>1 year warranty included.</div>
      </body>
    </html>
    """
    server = MCPToolServer.__new__(MCPToolServer)
    monkeypatch.setattr(server, "_fetch_html_safe", lambda link, headers: ("https://example.com/product", html))

    out = server._tool_inspect_product_page({"link": "https://example.com/product", "source": "example"}, {})
    assert out["shipping_price_pkr"] == 250.0
    assert "delivery charges" in str(out["shipping_summary"]).lower()
    assert "warranty" in str(out["warranty_info"]).lower()


def test_inspect_product_page_flags_listing_page(monkeypatch):
    html = """
    <html>
      <head><title>Gaming Earbuds Price List in Pakistan</title></head>
      <body>
        <div>Sort by featured</div>
        <div>Delivery charges Rs. 250 nationwide.</div>
      </body>
    </html>
    """
    server = MCPToolServer.__new__(MCPToolServer)
    monkeypatch.setattr(
        server,
        "_fetch_html_safe",
        lambda link, headers: ("https://example.com/collections/gaming-buds", html),
    )

    out = server._tool_inspect_product_page(
        {"link": "https://example.com/collections/gaming-buds", "source": "example", "title_hint": "Ronin R-540 EarBuds"},
        {},
    )
    assert out["page_type"] == "listing"
    assert "listing page" in str(out["page_warning"]).lower()
    assert out["detail_quality"] == "low"
