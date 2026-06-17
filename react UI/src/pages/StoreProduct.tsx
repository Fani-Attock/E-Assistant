import React from "react";
import { motion } from "framer-motion";
import { ExternalLink, MessageSquareText, Star, Store as StoreIcon, Trash2 } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import Footer from "../components/Footer";
import Header from "../components/Header";
import { createOrder, deleteMyProductReview, fetchProductReviews, fetchStoreProduct, submitProductReview } from "../lib/api";
import { useMarketplaceSession } from "../lib/marketplaceSession";
import type { MarketplaceUser, ProductReview, ProductReviewSummary, StoreProduct } from "../types/api";

function formatPkr(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) {
    return "N/A";
  }
  return `PKR ${value.toLocaleString()}`;
}

function formatPkrRange(min: number | null | undefined, max: number | null | undefined): string | null {
  if (min == null && max == null) {
    return null;
  }
  const low = min ?? max ?? null;
  const high = max ?? min ?? null;
  if (low == null || high == null) {
    return null;
  }
  if (Math.abs(low - high) < 0.5) {
    return formatPkr(low);
  }
  return `${formatPkr(low)} - ${formatPkr(high)}`;
}

const StoreProductPage: React.FC = () => {
  const { productId = "" } = useParams();
  const { token, isAuthenticated, user } = useMarketplaceSession();
  const [product, setProduct] = React.useState<StoreProduct | null>(null);
  const [seller, setSeller] = React.useState<MarketplaceUser | null>(null);
  const [reviewSummary, setReviewSummary] = React.useState<ProductReviewSummary | null>(null);
  const [reviews, setReviews] = React.useState<ProductReview[]>([]);
  const [myReview, setMyReview] = React.useState<ProductReview | null>(null);
  const [reviewDraft, setReviewDraft] = React.useState({ rating: 5, title: "", body: "" });
  const [reviewLoading, setReviewLoading] = React.useState(false);
  const [reviewError, setReviewError] = React.useState("");
  const [orderLoading, setOrderLoading] = React.useState(false);
  const [orderMessage, setOrderMessage] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    fetchStoreProduct(productId)
      .then((payload) => {
        if (!active) {
          return;
        }
        setProduct(payload.product);
        setSeller(payload.seller || null);
      })
      .catch((err) => {
        if (!active) {
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load product.");
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [productId]);

  React.useEffect(() => {
    if (!productId) {
      return;
    }
    let active = true;
    setReviewError("");
    fetchProductReviews(productId, token || undefined)
      .then((payload) => {
        if (!active) {
          return;
        }
        setReviewSummary(payload.summary);
        setReviews(payload.items);
        setMyReview(payload.my_review || null);
        if (payload.my_review) {
          setReviewDraft({
            rating: payload.my_review.rating,
            title: payload.my_review.title || "",
            body: payload.my_review.body || "",
          });
        } else {
          setReviewDraft({ rating: 5, title: "", body: "" });
        }
      })
      .catch((err) => {
        if (!active) {
          return;
        }
        setReviewError(err instanceof Error ? err.message : "Failed to load reviews.");
      });
    return () => {
      active = false;
    };
  }, [productId, token]);

  const primaryImage = product?.images?.[0] || product?.image || null;
  const sourceRating = product?.source_rating ?? product?.rating ?? null;
  const sourceReviewCount = product?.source_review_count ?? product?.review_count ?? null;
  const displaySourceRating = product?.display_source_rating ?? sourceRating ?? null;
  const displaySourceReviewCount = product?.display_source_review_count ?? sourceReviewCount ?? null;
  const displaySourceRatingKind = product?.display_source_rating_kind ?? "missing";
  const priceRangeText = formatPkrRange(product?.price_range_pkr_min, product?.price_range_pkr_max);
  const appRating = reviewSummary?.average_rating ?? product?.app_rating ?? null;
  const appReviewCount = reviewSummary?.review_count ?? product?.app_review_count ?? 0;
  const canReview = isAuthenticated && !(product?.listing_type === "seller" && product?.seller_id && product.seller_id === user?.user_id);
  const canOrder = isAuthenticated && !!token && product?.in_stock;

  const refreshReviews = React.useCallback(async () => {
    const payload = await fetchProductReviews(productId, token || undefined);
    setReviewSummary(payload.summary);
    setReviews(payload.items);
    setMyReview(payload.my_review || null);
    if (payload.my_review) {
      setReviewDraft({
        rating: payload.my_review.rating,
        title: payload.my_review.title || "",
        body: payload.my_review.body || "",
      });
    } else {
      setReviewDraft({ rating: 5, title: "", body: "" });
    }
  }, [productId, token]);

  const handleReviewSubmit = React.useCallback(async () => {
    if (!token || reviewLoading) {
      return;
    }
    setReviewLoading(true);
    setReviewError("");
    try {
      await submitProductReview(token, productId, reviewDraft);
      await refreshReviews();
    } catch (err) {
      setReviewError(err instanceof Error ? err.message : "Failed to submit review.");
    } finally {
      setReviewLoading(false);
    }
  }, [productId, refreshReviews, reviewDraft, reviewLoading, token]);

  const handleDeleteReview = React.useCallback(async () => {
    if (!token || reviewLoading) {
      return;
    }
    setReviewLoading(true);
    setReviewError("");
    try {
      await deleteMyProductReview(token, productId);
      setMyReview(null);
      setReviewDraft({ rating: 5, title: "", body: "" });
      await refreshReviews();
    } catch (err) {
      setReviewError(err instanceof Error ? err.message : "Failed to delete review.");
    } finally {
      setReviewLoading(false);
    }
  }, [productId, refreshReviews, reviewLoading, token]);

  const handleCreateOrder = React.useCallback(async () => {
    if (!token || !product || orderLoading) {
      return;
    }
    setOrderLoading(true);
    setOrderMessage("");
    try {
      await createOrder(token, { product_id: product.product_id, quantity: 1 });
      setOrderMessage("Order placed. It now contributes to seller sales and seasonal reports.");
    } catch (err) {
      setOrderMessage(err instanceof Error ? err.message : "Failed to place order.");
    } finally {
      setOrderLoading(false);
    }
  }, [orderLoading, product, token]);

  return (
    <div className="min-h-screen bg-[#0d1117] flex flex-col">
      <Header />
      <main className="flex-1 pt-16">
        <div className="max-w-6xl mx-auto px-6 py-10">
          {loading ? (
            <div className="h-96 rounded-xl bg-[#161b22] border border-[#30363d] animate-pulse" />
          ) : error ? (
            <div className="p-4 rounded-xl border border-[#f85149]/40 bg-[#f85149]/10 text-[#ffb3b3] text-sm">{error}</div>
          ) : product ? (
            <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}>
              <div className="mb-6">
                <Link to="/store" className="text-sm text-[#58a6ff] hover:underline">
                  Back to Store
                </Link>
              </div>

              <div className="grid lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] gap-8">
                <div className="rounded-2xl border border-[#30363d] bg-[#161b22] overflow-hidden">
                  {primaryImage ? (
                    <img src={primaryImage} alt={product.title} className="w-full h-[420px] object-cover" />
                  ) : (
                    <div className="w-full h-[420px] flex items-center justify-center text-[#6e7681]">No image available</div>
                  )}
                </div>

                <div>
                  <div className="flex flex-wrap items-center gap-2 mb-3">
                    <span className="px-2.5 py-1 rounded-full bg-[#58a6ff]/10 border border-[#58a6ff]/30 text-[#58a6ff] text-xs font-semibold">
                      {product.listing_type === "seller" ? "Seller Listing" : "Scraped Marketplace"}
                    </span>
                    <span className="px-2.5 py-1 rounded-full bg-[#0d1117] border border-[#30363d] text-[#8b949e] text-xs">
                      {product.source_label || product.source}
                    </span>
                  </div>
                  <h1 className="text-3xl font-bold text-white leading-tight mb-3">{product.title}</h1>
                  {product.description && <p className="text-sm text-[#8b949e] leading-7 mb-5">{product.description}</p>}

                  <div className="grid sm:grid-cols-2 gap-4 mb-6">
                    <div className="rounded-xl border border-[#30363d] bg-[#161b22] p-4">
                      <p className="text-xs uppercase tracking-wide text-[#6e7681] mb-2">Total price</p>
                      <p className="text-3xl font-bold text-[#3fb950]">{formatPkr(product.total_price_pkr ?? product.price_pkr)}</p>
                      <p className="text-xs text-[#8b949e] mt-1">Item: {formatPkr(product.price_pkr)} | Shipping: {formatPkr(product.shipping_pkr)}</p>
                      {priceRangeText && <p className="text-xs text-[#8b949e] mt-1">Matching-offer range: {priceRangeText}</p>}
                    </div>
                    <div className="rounded-xl border border-[#30363d] bg-[#161b22] p-4">
                      <p className="text-xs uppercase tracking-wide text-[#6e7681] mb-2">Availability</p>
                      <p className={`text-lg font-semibold ${product.in_stock ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                        {product.in_stock ? "In stock" : "Out of stock"}
                      </p>
                      {typeof product.stock_qty === "number" && (
                        <p className="text-xs text-[#8b949e] mt-1">Quantity listed: {product.stock_qty}</p>
                      )}
                    </div>
                  </div>

                  <div className="grid sm:grid-cols-2 gap-4 mb-6">
                    <div className="rounded-xl border border-[#30363d] bg-[#161b22] p-4">
                      <p className="text-xs uppercase tracking-wide text-[#6e7681] mb-2">Source rating</p>
                      <p className="text-2xl font-bold text-[#f0a500]">
                        {displaySourceRating != null ? displaySourceRating.toFixed(1) : "N/A"}
                      </p>
                      <p className="text-xs text-[#8b949e] mt-1">
                        {displaySourceReviewCount != null ? `${displaySourceReviewCount.toLocaleString()} source reviews` : "No source review data"}
                      </p>
                      {displaySourceRatingKind === "predicted" && (
                        <p className="text-[11px] text-[#8b949e] mt-1">Displayed rating is a prediction because scraped source rating is missing.</p>
                      )}
                    </div>
                    <div className="rounded-xl border border-[#30363d] bg-[#161b22] p-4">
                      <p className="text-xs uppercase tracking-wide text-[#6e7681] mb-2">In-app rating</p>
                      <p className="text-2xl font-bold text-[#58a6ff]">
                        {appRating != null ? appRating.toFixed(1) : "N/A"}
                      </p>
                      <p className="text-xs text-[#8b949e] mt-1">{appReviewCount.toLocaleString()} marketplace reviews</p>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-3 mb-8">
                    <Link
                      to={`/?q=${encodeURIComponent("tell me about this product")}&product=${encodeURIComponent(product.product_id)}`}
                      className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-[#58a6ff] text-[#0d1117] text-sm font-semibold hover:bg-[#79b8ff] transition-all duration-200"
                    >
                      <MessageSquareText size={16} />
                      Ask AI About This Product
                    </Link>
                    {(product.external_url || product.link) && (
                      <a
                        href={product.external_url || product.link || "#"}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-[#30363d] text-[#c9d1d9] hover:border-[#58a6ff]/40 hover:text-white transition-all duration-200"
                      >
                        <ExternalLink size={16} />
                        Open Source Listing
                      </a>
                    )}
                    {canOrder && (
                      <button
                        type="button"
                        onClick={handleCreateOrder}
                        disabled={orderLoading}
                        className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-[#1a7f37]/40 text-[#9be9a8] hover:border-[#3fb950] transition-all duration-200 disabled:opacity-60"
                      >
                        <StoreIcon size={16} />
                        {orderLoading ? "Placing order..." : "Buy on Marketplace"}
                      </button>
                    )}
                  </div>
                  {orderMessage && (
                    <div className="mb-6 p-3 rounded-lg border border-[#30363d] bg-[#161b22] text-sm text-[#c9d1d9]">
                      {orderMessage}
                    </div>
                  )}

                  <div className="grid md:grid-cols-2 gap-4">
                    <div className="rounded-xl border border-[#30363d] bg-[#161b22] p-4">
                      <h2 className="text-sm font-semibold text-white mb-3">Product details</h2>
                      <dl className="space-y-2 text-sm">
                        <div className="flex justify-between gap-4">
                          <dt className="text-[#6e7681]">Category</dt>
                          <dd className="text-[#c9d1d9] text-right">{product.category || "Not specified"}</dd>
                        </div>
                        <div className="flex justify-between gap-4">
                          <dt className="text-[#6e7681]">Brand</dt>
                          <dd className="text-[#c9d1d9] text-right">{product.brand || "Not specified"}</dd>
                        </div>
                        <div className="flex justify-between gap-4">
                          <dt className="text-[#6e7681]">Model</dt>
                          <dd className="text-[#c9d1d9] text-right">{product.model || "Not specified"}</dd>
                        </div>
                        <div className="flex justify-between gap-4">
                          <dt className="text-[#6e7681]">Rating</dt>
                          <dd className="text-[#c9d1d9] text-right">
                            Source {sourceRating?.toFixed(1) || "N/A"} | App {appRating?.toFixed(1) || "N/A"}
                          </dd>
                        </div>
                      </dl>
                    </div>

                    <div className="rounded-xl border border-[#30363d] bg-[#161b22] p-4">
                      <h2 className="text-sm font-semibold text-white mb-3">Seller / source</h2>
                      {seller ? (
                        <div className="space-y-2 text-sm">
                          <div className="flex items-center gap-2 text-[#c9d1d9]">
                            <StoreIcon size={16} className="text-[#58a6ff]" />
                            {seller.store_name || seller.full_name}
                          </div>
                          <p className="text-[#8b949e]">{seller.bio || "Seller account on the marketplace."}</p>
                          <Link to={`/store?seller_id=${encodeURIComponent(seller.user_id)}&listing_type=seller`} className="text-[#58a6ff] hover:underline">
                            View seller catalog
                          </Link>
                        </div>
                      ) : (
                        <div className="space-y-2 text-sm">
                          <p className="text-[#c9d1d9]">{product.source_label || product.source}</p>
                          <p className="text-[#8b949e]">This item comes from the scraped marketplace catalog.</p>
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="mt-6 rounded-xl border border-[#30363d] bg-[#161b22] p-4">
                    <h2 className="text-sm font-semibold text-white mb-3">Marketplace analytics</h2>
                    <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3 text-sm">
                      <div className="rounded-lg border border-[#30363d] bg-[#0d1117] p-3">
                        <p className="text-[#6e7681] text-xs uppercase tracking-wide mb-1">Units sold</p>
                        <p className="text-[#c9d1d9] font-semibold">{(product.units_sold ?? 0).toLocaleString()}</p>
                      </div>
                      <div className="rounded-lg border border-[#30363d] bg-[#0d1117] p-3">
                        <p className="text-[#6e7681] text-xs uppercase tracking-wide mb-1">Revenue</p>
                        <p className="text-[#c9d1d9] font-semibold">{formatPkr(product.revenue_pkr)}</p>
                      </div>
                      <div className="rounded-lg border border-[#30363d] bg-[#0d1117] p-3">
                        <p className="text-[#6e7681] text-xs uppercase tracking-wide mb-1">Predicted app rating</p>
                        <p className="text-[#c9d1d9] font-semibold">{product.predicted_app_rating != null ? product.predicted_app_rating.toFixed(1) : "N/A"}</p>
                      </div>
                      <div className="rounded-lg border border-[#30363d] bg-[#0d1117] p-3">
                        <p className="text-[#6e7681] text-xs uppercase tracking-wide mb-1">Demand score</p>
                        <p className="text-[#c9d1d9] font-semibold">{product.predicted_demand_score != null ? product.predicted_demand_score.toFixed(2) : "N/A"}</p>
                      </div>
                    </div>
                    {(product.best_month_labels?.length || 0) > 0 && (
                      <p className="text-sm text-[#8b949e] mt-4">
                        Best months for this product:{" "}
                        <span className="text-[#c9d1d9]">{(product.best_month_labels || []).join(", ")}</span>
                      </p>
                    )}
                  </div>

                  {product.specifications && (
                    <div className="mt-6 rounded-xl border border-[#30363d] bg-[#161b22] p-4">
                      <h2 className="text-sm font-semibold text-white mb-3">Specifications</h2>
                      <p className="text-sm text-[#8b949e] leading-7 whitespace-pre-wrap">{product.specifications}</p>
                    </div>
                  )}

                  <div className="mt-6 rounded-xl border border-[#30363d] bg-[#161b22] p-4">
                    <div className="flex items-center justify-between gap-4 mb-4">
                      <div>
                        <h2 className="text-sm font-semibold text-white">Marketplace reviews</h2>
                        <p className="text-xs text-[#8b949e] mt-1">
                          Separate from the rating scraped from the original source website.
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-lg font-bold text-[#58a6ff]">{appRating != null ? appRating.toFixed(1) : "N/A"}</p>
                        <p className="text-xs text-[#8b949e]">{appReviewCount.toLocaleString()} reviews</p>
                      </div>
                    </div>

                    {reviewError && (
                      <div className="mb-4 p-3 rounded-lg border border-[#f85149]/40 bg-[#f85149]/10 text-[#ffb3b3] text-sm">
                        {reviewError}
                      </div>
                    )}

                    {canReview ? (
                      <div className="mb-5 rounded-xl border border-[#30363d] bg-[#0d1117] p-4">
                        <div className="grid gap-3">
                          <div>
                            <label className="block text-xs uppercase tracking-wide text-[#6e7681] mb-2">Your rating</label>
                            <div className="flex gap-2">
                              {[1, 2, 3, 4, 5].map((value) => (
                                <button
                                  key={value}
                                  type="button"
                                  onClick={() => setReviewDraft((prev) => ({ ...prev, rating: value }))}
                                  className={`w-9 h-9 rounded-lg border flex items-center justify-center transition-colors ${
                                    reviewDraft.rating >= value
                                      ? "border-[#58a6ff] bg-[#58a6ff]/15 text-[#58a6ff]"
                                      : "border-[#30363d] text-[#6e7681] hover:text-white"
                                  }`}
                                >
                                  <Star size={16} className={reviewDraft.rating >= value ? "fill-current" : ""} />
                                </button>
                              ))}
                            </div>
                          </div>
                          <input
                            value={reviewDraft.title}
                            onChange={(event) => setReviewDraft((prev) => ({ ...prev, title: event.target.value }))}
                            placeholder="Review title"
                            className="w-full rounded-lg bg-[#161b22] border border-[#30363d] text-sm text-white px-3 py-2 outline-none focus:border-[#58a6ff]/50"
                          />
                          <textarea
                            value={reviewDraft.body}
                            onChange={(event) => setReviewDraft((prev) => ({ ...prev, body: event.target.value }))}
                            placeholder="Write your review"
                            rows={4}
                            className="w-full rounded-lg bg-[#161b22] border border-[#30363d] text-sm text-white px-3 py-2 outline-none focus:border-[#58a6ff]/50 resize-y"
                          />
                          <div className="flex flex-wrap gap-3">
                            <button
                              type="button"
                              onClick={handleReviewSubmit}
                              disabled={reviewLoading}
                              className="px-4 py-2 rounded-lg bg-[#58a6ff] text-[#0d1117] text-sm font-semibold disabled:opacity-60"
                            >
                              {myReview ? "Update Review" : "Submit Review"}
                            </button>
                            {myReview && (
                              <button
                                type="button"
                                onClick={handleDeleteReview}
                                disabled={reviewLoading}
                                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-[#f85149]/40 text-[#ffb3b3] text-sm font-medium disabled:opacity-60"
                              >
                                <Trash2 size={14} />
                                Delete Review
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className="mb-5 text-sm text-[#8b949e]">
                        {isAuthenticated
                          ? "You cannot rate your own seller listing."
                          : "Sign in with a marketplace account to leave an in-app review."}
                      </div>
                    )}

                    <div className="space-y-3">
                      {reviews.length > 0 ? (
                        reviews.map((review) => (
                          <div key={review.review_id} className="rounded-xl border border-[#30363d] bg-[#0d1117] p-4">
                            <div className="flex items-start justify-between gap-4 mb-2">
                              <div>
                                <p className="text-sm font-semibold text-white">{review.user_name || "Marketplace User"}</p>
                                <p className="text-[11px] uppercase tracking-wide text-[#6e7681]">{review.user_role || "member"}</p>
                              </div>
                              <div className="text-right">
                                <p className="text-sm font-semibold text-[#58a6ff]">{review.rating.toFixed(1)}</p>
                                <p className="text-[11px] text-[#6e7681]">{review.updated_at ? new Date(review.updated_at).toLocaleDateString() : ""}</p>
                              </div>
                            </div>
                            {review.title && <p className="text-sm font-medium text-[#c9d1d9] mb-1">{review.title}</p>}
                            {review.body && <p className="text-sm text-[#8b949e] leading-6">{review.body}</p>}
                          </div>
                        ))
                      ) : (
                        <div className="text-sm text-[#8b949e]">No marketplace reviews yet.</div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </motion.div>
          ) : null}
        </div>
      </main>
      <Footer />
    </div>
  );
};

export default StoreProductPage;
