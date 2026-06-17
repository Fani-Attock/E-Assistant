from src.mcp.server import MCPToolServer


def test_search_offers_normalizes_typo_heavy_query():
    class _FakePipeline:
        def __init__(self):
            self.calls = []

        def search(self, query, top_k, user_id=None):
            self.calls.append({"query": query, "top_k": top_k, "user_id": user_id})
            return []

    server = MCPToolServer.__new__(MCPToolServer)
    server.pipeline = _FakePipeline()

    out = server._tool_search_offers(
        {"query": "fin gamming earbuds in pakistan", "top_k": 5},
        {"user_id": "u1"},
    )

    assert out["normalized_query"] == "gaming earbuds"
    assert server.pipeline.calls[0]["query"] == "gaming earbuds"


def test_get_offer_details_returns_image_gallery():
    class _FakeCollection:
        def __init__(self, doc):
            self.doc = doc

        def find_one(self, _flt, _projection):
            return dict(self.doc)

    class _FakeDb(dict):
        def __getitem__(self, key):
            return dict.__getitem__(self, key)

    server = MCPToolServer.__new__(MCPToolServer)
    server.settings = type(
        "_Settings",
        (),
        {
            "normalized_collection": "offers_normalized",
            "marketplace_seller_products_collection": "seller_products",
        },
    )()
    server.db = _FakeDb(
        {
            "offers_normalized": _FakeCollection(
                {
                    "offer_id": "offer_42",
                    "title": "Gaming Earbuds X",
                    "link": "https://example.test/earbuds-x",
                    "source": "daraz",
                    "image": "https://cdn.example.test/earbuds-main.jpg",
                    "images": [
                        "https://cdn.example.test/earbuds-main.jpg",
                        "https://cdn.example.test/earbuds-side.jpg",
                    ],
                }
            ),
            "seller_products": _FakeCollection(None),
        }
    )

    out = server._tool_get_offer_details({"offer_id": "offer_42"}, {})
    assert out["found"] is True
    assert out["offer"]["image"] == "https://cdn.example.test/earbuds-main.jpg"
    assert out["offer"]["images"] == [
        "https://cdn.example.test/earbuds-main.jpg",
        "https://cdn.example.test/earbuds-side.jpg",
    ]
