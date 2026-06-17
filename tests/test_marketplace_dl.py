import torch

from src.ml.marketplace_dl.features import TEXT_DIM, NUMERIC_DIM, build_marketplace_feature_row
from src.ml.marketplace_dl.model import MarketplaceMultiTaskNet


def test_marketplace_feature_row_builds_targets():
    row = build_marketplace_feature_row(
        product={
            "product_id": "seller_1",
            "offer_id": "seller_1",
            "title": "Summer AC Inverter",
            "description": "Cooling product for hot weather",
            "category": "AC",
            "subcategory": "Inverter",
            "brand": "BrandX",
            "model": "AC-12",
            "price_pkr": 120000,
            "shipping_pkr": 1500,
            "in_stock": True,
            "stock_qty": 4,
        },
        app_rating=4.5,
        app_review_count=3,
        source_rating=4.7,
        source_review_count=12,
        orders=[{"status": "fulfilled", "quantity": 2, "total_pkr": 241500}],
        interactions=[{"event_type": "view"}, {"event_type": "purchase"}],
    )
    assert len(row["text"]) == TEXT_DIM
    assert len(row["numeric"]) == NUMERIC_DIM
    assert row["targets"]["rating"] == 4.5
    assert row["targets"]["demand"] > 0


def test_marketplace_multitask_model_forward():
    model = MarketplaceMultiTaskNet(text_dim=TEXT_DIM, numeric_dim=NUMERIC_DIM, hidden_dim=64)
    batch = {
        "text": torch.randn(2, TEXT_DIM),
        "numeric": torch.randn(2, NUMERIC_DIM),
        "seller_id": torch.tensor([1, 2], dtype=torch.long),
        "brand_id": torch.tensor([3, 4], dtype=torch.long),
        "category_id": torch.tensor([1, 1], dtype=torch.long),
        "subcategory_id": torch.tensor([5, 6], dtype=torch.long),
    }
    out = model(batch)
    assert out["rating"].shape == (2,)
    assert out["demand"].shape == (2,)
    assert out["month_logits"].shape == (2, 12)
