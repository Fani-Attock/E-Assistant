import type { StoreProduct } from "../types/api";
import type { Product } from "../types/chat";

function normalizeImageUrl(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }
  const raw = value.trim();
  if (!raw) {
    return null;
  }
  if (raw.startsWith("//")) {
    return `https:${raw}`;
  }
  if (raw.startsWith("http://") || raw.startsWith("https://") || raw.startsWith("/")) {
    return raw;
  }
  return null;
}

function sourceFallbackImageUrl(link: string | null | undefined, source: string | null | undefined): string | null {
  let host = "";
  if (link && /^https?:\/\//i.test(link)) {
    try {
      host = new URL(link).hostname.replace(/^www\./, "");
    } catch {
      host = "";
    }
  }
  if (!host && source) {
    host = source.toLowerCase().replace(/^www\./, "").replace(/[^a-z0-9.-]/g, "");
  }
  if (!host || !host.includes(".")) {
    return null;
  }
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=256`;
}

export function mapStoreProductToCard(product: StoreProduct): Product {
  const displaySourceRating = product.display_source_rating ?? product.source_rating ?? product.rating ?? null;
  const displaySourceReviewCount = product.display_source_review_count ?? product.source_review_count ?? product.review_count ?? null;
  return {
    id: product.product_id,
    name: product.title,
    image: normalizeImageUrl(product.image),
    fallbackImage: sourceFallbackImageUrl(product.external_url || product.link, product.source || product.store_name),
    price: typeof product.total_price_pkr === "number" ? product.total_price_pkr : product.price_pkr ?? null,
    rating: displaySourceRating,
    reviewCount: displaySourceReviewCount,
    sourceRating: product.source_rating ?? product.rating ?? null,
    sourceReviewCount: product.source_review_count ?? product.review_count ?? null,
    displaySourceRating,
    displaySourceReviewCount,
    displaySourceRatingKind: product.display_source_rating_kind ?? null,
    priceRangeMin: product.price_range_pkr_min ?? null,
    priceRangeMax: product.price_range_pkr_max ?? null,
    appRating: product.app_rating ?? null,
    appReviewCount: product.app_review_count ?? null,
    unitsSold: product.units_sold ?? null,
    orderCount: product.order_count ?? null,
    revenuePkr: product.revenue_pkr ?? null,
    predictedAppRating: product.predicted_app_rating ?? null,
    predictedDemandScore: product.predicted_demand_score ?? null,
    seasonalRelevanceScore: product.seasonal_relevance_score ?? null,
    bestMonthLabels: product.best_month_labels ?? null,
    source: (product.source_label || product.source || product.store_name || "STORE").toUpperCase(),
    url: product.external_url || product.internal_path || product.link || "#",
    listingType: product.listing_type,
    internalPath: product.internal_path,
    externalUrl: product.external_url,
    sellerId: product.seller_id,
    sellerName: product.seller_name,
    storeName: product.store_name,
    reason:
      product.listing_type === "seller"
        ? `Marketplace seller listing${product.store_name ? ` from ${product.store_name}` : ""}.`
        : product.description || product.specifications || undefined,
  };
}
