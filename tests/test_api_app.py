from dataclasses import replace
import importlib
from pathlib import Path
import re
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.core.settings import Settings


class _NoopRateLimiter:
    def allow(self, key: str) -> bool:
        return True


class _FakePipeline:
    def search(self, query: str, top_k: int, user_id: str | None = None):
        return [{"title": "Example Result", "link": "https://example.test/p1", "source": "example"}]


class _FakeAssistant:
    def __init__(self):
        self.run_calls = []
        self.history_calls = []
        self.delete_calls = []
        self.search_status_calls = []
        self.raise_permission = False

    def run(
        self,
        *,
        query,
        conversation_id=None,
        user_id=None,
        reference_product_id=None,
        top_k=5,
        min_rating=None,
        include_tool_trace=False,
    ):
        self.run_calls.append(
            {
                "query": query,
                "conversation_id": conversation_id,
                "user_id": user_id,
                "reference_product_id": reference_product_id,
                "top_k": top_k,
                "min_rating": min_rating,
                "include_tool_trace": include_tool_trace,
            }
        )
        if self.raise_permission:
            raise PermissionError("user_id is required")
        return {
            "conversation_id": conversation_id or "conv_test",
            "mode": "general_question",
            "answer": "ok",
            "results": [],
        }

    def get_history(self, conversation_id: str, *, limit: int, user_id: str | None = None):
        self.history_calls.append({"conversation_id": conversation_id, "limit": limit, "user_id": user_id})
        return {"conversation_id": conversation_id, "count": 0, "turns": []}

    def delete_conversation(self, conversation_id: str, *, user_id: str | None = None):
        self.delete_calls.append({"conversation_id": conversation_id, "user_id": user_id})
        return {"conversation_id": conversation_id, "turns_deleted": 0, "session_deleted": 0}

    def get_search_status(self, conversation_id: str, *, user_id: str | None = None):
        self.search_status_calls.append({"conversation_id": conversation_id, "user_id": user_id})
        return {
            "conversation_id": conversation_id,
            "query": "earbuds",
            "search_phase": "local_partial",
            "search_status": "online_searching",
            "pending_online_refresh": True,
            "local_results": [],
            "online_results": [],
            "merged_results": [],
            "notice": None,
            "report_id": None,
        }


class _FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, spec):
        for key, direction in reversed(spec):
            reverse = isinstance(direction, int) and direction < 0
            self.rows.sort(key=lambda row: (row.get(key) is None, row.get(key)), reverse=reverse)
        return self

    def limit(self, count: int):
        self.rows = self.rows[:count]
        return self

    def __iter__(self):
        return iter(self.rows)


class _FakeCollection:
    def __init__(self, rows=None):
        self.rows = [dict(row) for row in (rows or [])]

    def _matches(self, flt, row):
        if not flt:
            return True
        for key, value in flt.items():
            if key == "$and":
                return all(self._matches(item, row) for item in value)
            if key == "$or":
                return any(self._matches(item, row) for item in value)
            current = row.get(key)
            if isinstance(value, dict):
                if "$regex" in value:
                    pattern = value["$regex"]
                    flags = re.IGNORECASE if "i" in str(value.get("$options", "")) else 0
                    if not re.search(pattern, str(current or ""), flags=flags):
                        return False
                    continue
                if "$in" in value and current not in value["$in"]:
                    return False
                if "$lte" in value and not (current is not None and current <= value["$lte"]):
                    return False
                if "$gte" in value and not (current is not None and current >= value["$gte"]):
                    return False
                continue
            if current != value:
                return False
        return True

    @staticmethod
    def _project(row, projection):
        if not projection:
            return dict(row)
        include = {key for key, flag in projection.items() if flag and key != "_id"}
        if include:
            return {key: row.get(key) for key in include}
        clone = dict(row)
        for key, flag in projection.items():
            if flag == 0:
                clone.pop(key, None)
        return clone

    def find_one(self, flt=None, projection=None):
        for row in self.rows:
            if self._matches(flt or {}, row):
                return self._project(row, projection)
        return None

    def insert_one(self, doc):
        self.rows.append(dict(doc))
        return SimpleNamespace(inserted_id=len(self.rows))

    def find(self, flt=None, projection=None):
        rows = [self._project(row, projection) for row in self.rows if self._matches(flt or {}, row)]
        return _FakeCursor(rows)

    def update_one(self, flt, update, upsert=False):
        for row in self.rows:
            if self._matches(flt, row):
                if "$set" in update:
                    row.update(update["$set"])
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            new_row = dict(flt)
            if "$set" in update:
                new_row.update(update["$set"])
            self.rows.append(new_row)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=len(self.rows))
        return SimpleNamespace(matched_count=0, modified_count=0)

    def delete_one(self, flt):
        for index, row in enumerate(self.rows):
            if self._matches(flt, row):
                del self.rows[index]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class _FakeDb(dict):
    def __getitem__(self, key):
        if key not in self:
            self[key] = _FakeCollection()
        return dict.__getitem__(self, key)


def _build_client(monkeypatch, *, fake_db=None, **settings_overrides):
    from src.core import audit as audit_module
    from src.core import db as db_module

    monkeypatch.setattr(db_module, "ensure_indexes", lambda settings: None)
    monkeypatch.setattr(audit_module, "write_audit_log", lambda *args, **kwargs: None)
    api_app = importlib.import_module("src.api.app")
    monkeypatch.setattr(api_app, "ensure_indexes", lambda settings: None)
    monkeypatch.setattr(api_app, "write_audit_log", lambda *args, **kwargs: None)
    assistant = _FakeAssistant()
    base_overrides = {
        "rate_limit_enabled": False,
        "require_auth_for_write": True,
        "service_api_key": "svc-test",
        "admin_api_key": "admin-test",
    }
    base_overrides.update(settings_overrides)
    settings = replace(Settings(), **base_overrides)
    app = api_app.create_app(
        settings=settings,
        pipeline=_FakePipeline(),
        assistant_agent=assistant,
        app_db=fake_db or _FakeDb(),
        mongo_client=None,
        rate_limiter=_NoopRateLimiter(),
    )
    return TestClient(app), assistant


def test_assistant_endpoint_requires_api_key(monkeypatch):
    client, _assistant = _build_client(monkeypatch)
    response = client.post("/assistant", json={"query": "search for earbuds"})
    assert response.status_code == 401
    assert response.json()["detail"] == "unauthorized"


def test_assistant_endpoint_passes_request_to_agent(monkeypatch):
    client, assistant = _build_client(monkeypatch)
    response = client.post(
        "/assistant",
        headers={"X-API-Key": "svc-test"},
        json={
            "query": "search for earbuds",
            "conversation_id": "conv_12345678",
            "user_id": "u123",
            "reference_product_id": "seller_abc12345",
            "top_k": 7,
            "include_tool_trace": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["conversation_id"] == "conv_12345678"
    assert assistant.run_calls[0]["query"] == "search for earbuds"
    assert assistant.run_calls[0]["user_id"] == "u123"
    assert assistant.run_calls[0]["reference_product_id"] == "seller_abc12345"
    assert assistant.run_calls[0]["top_k"] == 7
    assert assistant.run_calls[0]["include_tool_trace"] is True


def test_assistant_endpoint_surfaces_permission_error(monkeypatch):
    client, assistant = _build_client(monkeypatch)
    assistant.raise_permission = True
    response = client.post(
        "/assistant",
        headers={"X-API-Key": "svc-test"},
        json={"query": "search for earbuds"},
    )
    assert response.status_code == 403
    assert "assistant_forbidden" in response.json()["detail"]


def test_assistant_history_endpoint_passes_user_id(monkeypatch):
    client, assistant = _build_client(monkeypatch)
    response = client.get(
        "/assistant/conversations/conv_12345678",
        headers={"X-API-Key": "svc-test"},
        params={"user_id": "u123", "limit": 50},
    )
    assert response.status_code == 200
    assert assistant.history_calls[0] == {"conversation_id": "conv_12345678", "limit": 50, "user_id": "u123"}


def test_assistant_search_status_endpoint_passes_user_id(monkeypatch):
    client, assistant = _build_client(monkeypatch)
    response = client.get(
        "/assistant/conversations/conv_12345678/search-status",
        headers={"X-API-Key": "svc-test"},
        params={"user_id": "u123"},
    )
    assert response.status_code == 200
    assert response.json()["search_status"] == "online_searching"
    assert assistant.search_status_calls[0] == {"conversation_id": "conv_12345678", "user_id": "u123"}


def test_health_details_reports_production_switches(monkeypatch):
    client, _assistant = _build_client(
        monkeypatch,
        conversation_require_user_id=True,
        require_auth_for_write=True,
    )
    response = client.get("/health/details", headers={"X-Admin-API-Key": "admin-test"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["require_auth_for_write"] is True
    assert payload["conversation_require_user_id"] is True


def test_marketplace_register_and_me(monkeypatch):
    fake_db = _FakeDb()
    client, _assistant = _build_client(monkeypatch, fake_db=fake_db)
    register = client.post(
        "/store/auth/register",
        json={
            "full_name": "A Buyer",
            "email": "buyer@example.com",
            "password": "Password123",
            "role": "buyer",
        },
    )
    assert register.status_code == 200
    payload = register.json()
    assert payload["user"]["role"] == "buyer"
    me = client.get("/store/auth/me", headers={"Authorization": f"Bearer {payload['token']}"})
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "buyer@example.com"


def test_store_catalog_includes_scraped_products(monkeypatch):
    fake_db = _FakeDb(
        {
            "offers_normalized": _FakeCollection(
                [
                    {
                        "offer_id": "offer_1",
                        "title": "Samsung Galaxy Watch 6",
                        "title_normalized": "samsung galaxy watch 6",
                        "link": "https://example.test/watch6",
                        "source": "daraz",
                        "image": "https://cdn.example.test/watch6-main.jpg",
                        "images": [
                            "https://cdn.example.test/watch6-main.jpg",
                            "https://cdn.example.test/watch6-side.jpg",
                        ],
                        "price_pkr": 65000,
                        "shipping_pkr": 250,
                        "rating": 4.6,
                        "review_count": 180,
                        "in_stock": True,
                    },
                    {
                        "offer_id": "offer_1_b",
                        "title": "Samsung Galaxy Watch 6",
                        "title_normalized": "samsung galaxy watch 6",
                        "link": "https://example.test/watch6-b",
                        "source": "shophive",
                        "image": "https://cdn.example.test/watch6-b.jpg",
                        "images": ["https://cdn.example.test/watch6-b.jpg"],
                        "price_pkr": 69000,
                        "shipping_pkr": 0,
                        "rating": None,
                        "review_count": 0,
                        "in_stock": True,
                    }
                ]
            )
        }
    )
    client, _assistant = _build_client(monkeypatch, fake_db=fake_db)
    response = client.get("/store/catalog", params={"q": "watch"})
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["listing_type"] == "scraped"
    assert items[0]["title"] == "Samsung Galaxy Watch 6"
    assert items[0]["images"] == [
        "https://cdn.example.test/watch6-main.jpg",
        "https://cdn.example.test/watch6-side.jpg",
    ]
    assert items[0]["price_range_pkr_min"] == 65250.0
    assert items[0]["price_range_pkr_max"] == 69000.0


def test_store_product_detail_includes_scraped_image_gallery(monkeypatch):
    fake_db = _FakeDb(
        {
            "offers_normalized": _FakeCollection(
                [
                    {
                        "offer_id": "offer_2",
                        "title": "Gaming Earbuds X",
                        "title_normalized": "gaming earbuds x",
                        "link": "https://example.test/earbuds-x",
                        "source": "priceoye",
                        "image": "https://cdn.example.test/earbuds-x-main.jpg",
                        "images": [
                            "https://cdn.example.test/earbuds-x-main.jpg",
                            "https://cdn.example.test/earbuds-x-open.jpg",
                        ],
                        "price_pkr": 4999,
                        "shipping_pkr": 199,
                        "rating": 4.4,
                        "review_count": 82,
                        "in_stock": True,
                    }
                ]
            )
        }
    )
    client, _assistant = _build_client(monkeypatch, fake_db=fake_db)
    response = client.get("/store/products/scraped_offer_2")
    assert response.status_code == 200
    product = response.json()["product"]
    assert product["image"] == "https://cdn.example.test/earbuds-x-main.jpg"
    assert product["images"] == [
        "https://cdn.example.test/earbuds-x-main.jpg",
        "https://cdn.example.test/earbuds-x-open.jpg",
    ]


def test_store_product_reviews_expose_app_rating_summary(monkeypatch):
    fake_db = _FakeDb(
        {
            "offers_normalized": _FakeCollection(
                [
                    {
                        "offer_id": "offer_3",
                        "title": "Wireless Earbuds Lite",
                        "title_normalized": "wireless earbuds lite",
                        "link": "https://example.test/earbuds-lite",
                        "source": "daraz",
                        "price_pkr": 3200,
                        "shipping_pkr": 150,
                        "rating": 4.2,
                        "review_count": 40,
                        "in_stock": True,
                    }
                ]
            ),
            "marketplace_product_reviews": _FakeCollection(
                [
                    {
                        "review_id": "r1",
                        "product_id": "scraped_offer_3",
                        "offer_id": "offer_3",
                        "user_id": "u1",
                        "user_name": "Buyer One",
                        "user_role": "buyer",
                        "listing_type": "scraped",
                        "rating": 5,
                    },
                    {
                        "review_id": "r2",
                        "product_id": "scraped_offer_3",
                        "offer_id": "offer_3",
                        "user_id": "u2",
                        "user_name": "Buyer Two",
                        "user_role": "buyer",
                        "listing_type": "scraped",
                        "rating": 3,
                    },
                ]
            ),
        }
    )
    client, _assistant = _build_client(monkeypatch, fake_db=fake_db)
    detail = client.get("/store/products/scraped_offer_3")
    assert detail.status_code == 200
    product = detail.json()["product"]
    assert product["source_rating"] == 4.2
    assert product["app_rating"] == 4.0
    assert product["app_review_count"] == 2
    assert product["display_source_rating"] == 4.2
    assert product["display_source_rating_kind"] == "scraped"

    reviews = client.get("/store/products/scraped_offer_3/reviews")
    assert reviews.status_code == 200
    payload = reviews.json()
    assert payload["summary"]["average_rating"] == 4.0
    assert payload["summary"]["review_count"] == 2
    assert len(payload["items"]) == 2


def test_buyer_can_submit_and_delete_product_review(monkeypatch):
    fake_db = _FakeDb(
        {
            "offers_normalized": _FakeCollection(
                [
                    {
                        "offer_id": "offer_4",
                        "title": "Gaming Headset Z",
                        "title_normalized": "gaming headset z",
                        "link": "https://example.test/headset-z",
                        "source": "shophive",
                        "price_pkr": 8999,
                        "shipping_pkr": 0,
                        "rating": 4.7,
                        "review_count": 15,
                        "in_stock": True,
                    }
                ]
            )
        }
    )
    client, _assistant = _build_client(monkeypatch, fake_db=fake_db)
    register = client.post(
        "/store/auth/register",
        json={
            "full_name": "Buyer One",
            "email": "buyer1@example.com",
            "password": "Password123",
            "role": "buyer",
        },
    )
    token = register.json()["token"]
    create_review = client.post(
        "/store/products/scraped_offer_4/reviews",
        headers={"Authorization": f"Bearer {token}"},
        json={"rating": 5, "title": "Excellent", "body": "Great value for gaming."},
    )
    assert create_review.status_code == 200
    assert create_review.json()["summary"]["average_rating"] == 5.0

    reviews = client.get("/store/products/scraped_offer_4/reviews", headers={"Authorization": f"Bearer {token}"})
    assert reviews.status_code == 200
    payload = reviews.json()
    assert payload["my_review"]["rating"] == 5
    assert payload["summary"]["review_count"] == 1

    delete_review = client.delete("/store/products/scraped_offer_4/reviews/me", headers={"Authorization": f"Bearer {token}"})
    assert delete_review.status_code == 200
    assert delete_review.json()["deleted"] is True


def test_store_product_uses_predicted_rating_fallback(monkeypatch):
    fake_db = _FakeDb(
        {
            "offers_normalized": _FakeCollection(
                [
                    {
                        "offer_id": "offer_pred",
                        "title": "Cooling Fan X1",
                        "title_normalized": "cooling fan x1",
                        "link": "https://example.test/fan-x1",
                        "source": "daraz",
                        "price_pkr": 5000,
                        "shipping_pkr": 200,
                        "rating": None,
                        "review_count": 0,
                        "in_stock": True,
                        "category": "Cooling",
                    }
                ]
            ),
            "marketplace_predictions": _FakeCollection(
                [
                    {
                        "product_id": "scraped_offer_pred",
                        "predicted_app_rating": 4.3,
                    }
                ]
            ),
        }
    )
    client, _assistant = _build_client(monkeypatch, fake_db=fake_db)
    detail = client.get("/store/products/scraped_offer_pred")
    assert detail.status_code == 200
    product = detail.json()["product"]
    assert product["display_source_rating"] == 4.3
    assert product["display_source_rating_kind"] == "predicted"


def test_seller_can_manage_products_and_catalog_shows_listing(monkeypatch):
    fake_db = _FakeDb()
    client, _assistant = _build_client(monkeypatch, fake_db=fake_db)
    register = client.post(
        "/store/auth/register",
        json={
            "full_name": "Seller One",
            "email": "seller@example.com",
            "password": "Password123",
            "role": "seller",
            "store_name": "Seller Store",
        },
    )
    token = register.json()["token"]
    create = client.post(
        "/store/seller/products",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "Gaming Earbuds Pro",
            "description": "Low-latency earbuds",
            "category": "Audio",
            "price_pkr": 4999,
            "shipping_pkr": 250,
            "stock_qty": 12,
            "in_stock": True,
            "images": ["https://cdn.test/earbuds.jpg"],
        },
    )
    assert create.status_code == 200
    product = create.json()["product"]
    assert product["listing_type"] == "seller"

    mine = client.get("/store/seller/products", headers={"Authorization": f"Bearer {token}"})
    assert mine.status_code == 200
    assert len(mine.json()["items"]) == 1

    catalog = client.get("/store/catalog", params={"listing_type": "seller", "q": "earbuds"})
    assert catalog.status_code == 200
    assert catalog.json()["items"][0]["title"] == "Gaming Earbuds Pro"

    detail = client.get(f"/store/products/{product['product_id']}")
    assert detail.status_code == 200
    assert detail.json()["product"]["seller_id"] == register.json()["user"]["user_id"]


def test_seller_publish_validation_error_is_structured(monkeypatch):
    fake_db = _FakeDb()
    client, _assistant = _build_client(monkeypatch, fake_db=fake_db)
    register = client.post(
        "/store/auth/register",
        json={
            "full_name": "Seller One",
            "email": "seller2@example.com",
            "password": "Password123",
            "role": "seller",
            "store_name": "Seller Store",
        },
    )
    token = register.json()["token"]
    create = client.post(
        "/store/seller/products",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "X",
            "price_pkr": 2000,
        },
    )
    assert create.status_code == 422
    payload = create.json()["detail"]
    assert payload["message"]
    assert isinstance(payload["field_errors"], list)
    assert payload["field_errors"][0]["field"] == "title"


def test_marketplace_order_and_seller_report_summary(monkeypatch):
    fake_db = _FakeDb()
    client, _assistant = _build_client(monkeypatch, fake_db=fake_db)
    seller_register = client.post(
        "/store/auth/register",
        json={
            "full_name": "Seller Alpha",
            "email": "selleralpha@example.com",
            "password": "Password123",
            "role": "seller",
            "store_name": "Alpha Store",
        },
    )
    seller_token = seller_register.json()["token"]
    buyer_register = client.post(
        "/store/auth/register",
        json={
            "full_name": "Buyer Beta",
            "email": "buyerbeta@example.com",
            "password": "Password123",
            "role": "buyer",
        },
    )
    buyer_token = buyer_register.json()["token"]
    create = client.post(
        "/store/seller/products",
        headers={"Authorization": f"Bearer {seller_token}"},
        json={
            "title": "Summer AC",
            "description": "Cooling unit",
            "category": "AC",
            "price_pkr": 120000,
            "shipping_pkr": 1500,
            "stock_qty": 5,
            "in_stock": True,
        },
    )
    product = create.json()["product"]
    order = client.post(
        "/store/orders",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={"product_id": product["product_id"], "quantity": 2},
    )
    assert order.status_code == 200
    order_id = order.json()["order"]["order_id"]

    pending_report = client.get("/store/seller/reports/summary", headers={"Authorization": f"Bearer {seller_token}"})
    assert pending_report.status_code == 200
    assert pending_report.json()["summary"]["units_sold"] == 0

    paid = client.put(
        f"/store/seller/orders/{order_id}",
        headers={"Authorization": f"Bearer {seller_token}"},
        json={"status": "paid"},
    )
    assert paid.status_code == 200
    fulfilled = client.put(
        f"/store/seller/orders/{order_id}",
        headers={"Authorization": f"Bearer {seller_token}"},
        json={"status": "fulfilled"},
    )
    assert fulfilled.status_code == 200

    report = client.get("/store/seller/reports/summary", headers={"Authorization": f"Bearer {seller_token}"})
    assert report.status_code == 200
    payload = report.json()
    assert payload["summary"]["units_sold"] == 2
    assert payload["summary"]["order_count"] == 1
    assert payload["products"][0]["best_months"] is not None


def test_saved_reports_endpoints_list_detail_delete_and_pdf(monkeypatch, tmp_path):
    pdf_path = Path(tmp_path) / "rpt_1.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")
    fake_db = _FakeDb(
        {
            "saved_reports": _FakeCollection(
                [
                    {
                        "report_id": "rpt_1",
                        "owner_user_id": "u123",
                        "report_type": "assistant_report",
                        "title": "Assistant report",
                        "payload": {"summary": {"result_count": 2}},
                        "pdf_path": str(pdf_path),
                    }
                ]
            )
        }
    )
    client, _assistant = _build_client(monkeypatch, fake_db=fake_db, report_artifacts_dir=str(tmp_path))
    listing = client.get("/reports", headers={"X-API-Key": "svc-test"}, params={"user_id": "u123"})
    assert listing.status_code == 200
    assert listing.json()["count"] == 1

    detail = client.get("/reports/rpt_1", headers={"X-API-Key": "svc-test"}, params={"user_id": "u123"})
    assert detail.status_code == 200
    assert detail.json()["report"]["title"] == "Assistant report"

    pdf = client.get("/reports/rpt_1/pdf", headers={"X-API-Key": "svc-test"}, params={"user_id": "u123"})
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")

    deleted = client.delete("/reports/rpt_1", headers={"X-API-Key": "svc-test"}, params={"user_id": "u123"})
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert not pdf_path.exists()


def test_seller_summary_auto_saves_report(monkeypatch, tmp_path):
    fake_db = _FakeDb()
    client, _assistant = _build_client(monkeypatch, fake_db=fake_db, report_artifacts_dir=str(tmp_path))
    register = client.post(
        "/store/auth/register",
        json={
            "full_name": "Seller One",
            "email": "seller-summary@example.com",
            "password": "Password123",
            "role": "seller",
            "store_name": "Seller Store",
        },
    )
    token = register.json()["token"]
    summary = client.get("/store/seller/reports/summary", headers={"Authorization": f"Bearer {token}"})
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["report_id"].startswith("rpt_")
    reports = client.get(
        "/reports",
        headers={"X-API-Key": "svc-test"},
        params={"user_id": register.json()["user"]["user_id"]},
    )
    assert reports.status_code == 200
    assert any(row["report_id"] == payload["report_id"] for row in reports.json()["items"])
