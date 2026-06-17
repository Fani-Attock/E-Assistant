from datetime import datetime

from pydantic import BaseModel, Field


class ProductOffer(BaseModel):
    title: str
    link: str
    source: str
    category: str | None = None
    subcategory: str | None = None
    raw_price: str | None = None
    price_pkr: float | None = None
    shipping_pkr: float = 0.0
    in_stock: bool = True
    rating: float | None = None
    review_count: int = 0
    image: str | None = None
    images: list[str] = Field(default_factory=list)
    specifications: str | None = None
    last_scraped: str | None = None


class SearchRequest(BaseModel):
    q: str = Field(min_length=2, description="Natural-language product query")
    top_k: int = Field(default=5, ge=1, le=50)


class SearchResult(BaseModel):
    title: str
    link: str
    source: str
    image: str | None = None
    images: list[str] = Field(default_factory=list)
    price_pkr: float | None = None
    total_price_pkr: float | None = None
    rating: float | None = None
    review_count: int | None = None
    match_score: float | None = None
    rank_score: float | None = None
    reason: str | None = None


class InteractionIn(BaseModel):
    user_id: str = Field(min_length=1)
    offer_id: str | None = None
    link: str | None = None
    source: str | None = None
    event_type: str = Field(pattern="^(view|click|save|purchase)$")
    event_ts: datetime | None = None
    event_id: str | None = Field(default=None, min_length=8, max_length=128)


class AssistantRequest(BaseModel):
    query: str = Field(min_length=2, description="Natural-language request")
    conversation_id: str | None = Field(default=None, min_length=8, max_length=128)
    user_id: str | None = Field(default=None, min_length=1, max_length=128)
    reference_product_id: str | None = Field(default=None, min_length=4, max_length=160)
    top_k: int = Field(default=5, ge=1, le=20)
    min_rating: float | None = Field(default=None, ge=0, le=5)
    include_tool_trace: bool = False


class MarketplaceRegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=180)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(pattern="^(buyer|seller|merchant)$")
    store_name: str | None = Field(default=None, min_length=2, max_length=120)
    bio: str | None = Field(default=None, max_length=400)


class MarketplaceLoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=180)
    password: str = Field(min_length=8, max_length=128)


class MarketplaceSellerProductIn(BaseModel):
    title: str = Field(min_length=2, max_length=220)
    description: str | None = Field(default=None, max_length=4000)
    category: str | None = Field(default=None, max_length=80)
    subcategory: str | None = Field(default=None, max_length=80)
    brand: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    price_pkr: float = Field(ge=0)
    shipping_pkr: float = Field(default=0.0, ge=0)
    in_stock: bool = True
    stock_qty: int = Field(default=0, ge=0, le=1_000_000)
    images: list[str] = Field(default_factory=list, max_length=8)
    specifications: str | None = Field(default=None, max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=24)
    external_url: str | None = Field(default=None, max_length=1000)


class MarketplaceSellerProductUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=220)
    description: str | None = Field(default=None, max_length=4000)
    category: str | None = Field(default=None, max_length=80)
    subcategory: str | None = Field(default=None, max_length=80)
    brand: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    price_pkr: float | None = Field(default=None, ge=0)
    shipping_pkr: float | None = Field(default=None, ge=0)
    in_stock: bool | None = None
    stock_qty: int | None = Field(default=None, ge=0, le=1_000_000)
    images: list[str] | None = Field(default=None, max_length=8)
    specifications: str | None = Field(default=None, max_length=4000)
    tags: list[str] | None = Field(default=None, max_length=24)
    external_url: str | None = Field(default=None, max_length=1000)


class MarketplaceProductReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    title: str | None = Field(default=None, max_length=160)
    body: str | None = Field(default=None, max_length=2000)


class MarketplaceOrderIn(BaseModel):
    product_id: str = Field(min_length=4, max_length=160)
    quantity: int = Field(default=1, ge=1, le=1000)
    shipping_address: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=1000)


class MarketplaceOrderStatusUpdate(BaseModel):
    status: str = Field(pattern="^(pending|paid|fulfilled|cancelled)$")


class MarketplacePredictionTrainRequest(BaseModel):
    epochs: int = Field(default=20, ge=1, le=500)
    batch_size: int = Field(default=32, ge=4, le=512)
    learning_rate: float = Field(default=0.001, gt=0, le=0.1)
    hidden_dim: int = Field(default=128, ge=16, le=1024)
