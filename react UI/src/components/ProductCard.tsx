import React from "react";
import { motion } from "framer-motion";
import { ExternalLink, ImageOff, ShoppingCart, Star, TrendingUp } from "lucide-react";
import { Link } from "react-router-dom";

import type { Product } from "../types/chat";

interface ProductCardProps {
  product: Product;
  rank: number;
  delay?: number;
}

function renderPrice(value: number | null): string {
  if (value == null || !Number.isFinite(value)) {
    return "Price N/A";
  }
  return `PKR ${value.toLocaleString()}`;
}

function renderPriceRange(min: number | null | undefined, max: number | null | undefined): string | null {
  if (min == null && max == null) {
    return null;
  }
  const low = min ?? max ?? null;
  const high = max ?? min ?? null;
  if (low == null || high == null) {
    return null;
  }
  if (Math.abs(low - high) < 0.5) {
    return `PKR ${low.toLocaleString()}`;
  }
  return `PKR ${low.toLocaleString()} - ${high.toLocaleString()}`;
}

const ProductCard: React.FC<ProductCardProps> = ({ product, rank, delay = 0 }) => {
  const [imageSrc, setImageSrc] = React.useState<string | null>(product.image || product.fallbackImage);
  const sourceRating = product.sourceRating ?? product.rating ?? null;
  const sourceReviewCount = product.sourceReviewCount ?? product.reviewCount ?? null;
  const displaySourceRating = product.displaySourceRating ?? sourceRating;
  const displaySourceReviewCount = product.displaySourceReviewCount ?? sourceReviewCount;
  const displaySourceRatingKind = product.displaySourceRatingKind ?? "missing";
  const appRating = product.appRating ?? null;
  const appReviewCount = product.appReviewCount ?? null;
  const ratingValue = displaySourceRating ?? 0;
  const hasRating = displaySourceRating != null && Number.isFinite(displaySourceRating);
  const hasAppRating = appRating != null && Number.isFinite(appRating);
  const hasPrice = product.price != null && Number.isFinite(product.price);
  const priceRange = renderPriceRange(product.priceRangeMin, product.priceRangeMax);
  const isFallbackImage = imageSrc != null && imageSrc === product.fallbackImage;
  const isInternal = Boolean(product.internalPath || (product.url && product.url.startsWith("/")));
  const targetUrl = product.internalPath || product.url;

  React.useEffect(() => {
    setImageSrc(product.image || product.fallbackImage);
  }, [product.image, product.fallbackImage]);

  const renderStars = (rating: number) =>
    Array.from({ length: 5 }, (_, i) => (
      <Star
        key={i}
        size={12}
        className={i < Math.floor(rating) ? "text-[#f0a500] fill-[#f0a500]" : "text-[#30363d]"}
      />
    ));

  const valueBadge =
    rank === 1
      ? { label: "Best Value", color: "bg-[#1a7f37]/20 text-[#3fb950] border-[#1a7f37]/40" }
      : rank === 2
      ? { label: "Top Pick", color: "bg-[#58a6ff]/10 text-[#58a6ff] border-[#58a6ff]/30" }
      : null;

  const discountPct =
    hasPrice && product.originalPrice && product.originalPrice > product.price!
      ? Math.round(((product.originalPrice - product.price!) / product.originalPrice) * 100)
      : null;

  return (
    <motion.article
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay, ease: "easeOut" }}
      className="group bg-[#161b22] border border-[#30363d] rounded-xl overflow-hidden hover:border-[#58a6ff]/40 hover:shadow-lg hover:shadow-[#58a6ff]/5 hover:-translate-y-0.5 transition-all duration-300"
      aria-label={`Product: ${product.name}`}
    >
      <div className="relative">
        {imageSrc ? (
          <img
            src={imageSrc}
            alt={product.name}
            width={400}
            height={200}
            className={`w-full h-40 ${
              isFallbackImage ? "object-contain p-10 bg-[#111827]" : "object-cover"
            }`}
            loading="lazy"
            referrerPolicy="no-referrer"
            onError={() => {
              if (imageSrc !== product.fallbackImage && product.fallbackImage) {
                setImageSrc(product.fallbackImage);
                return;
              }
              setImageSrc(null);
            }}
          />
        ) : (
          <div className="w-full h-40 bg-gradient-to-br from-[#1f2937] via-[#111827] to-[#0f172a] flex items-center justify-center border-b border-[#30363d]">
            <div className="flex items-center gap-2 text-[#6e7681] text-xs font-medium">
              <ImageOff size={14} />
              No image available
            </div>
          </div>
        )}
        <div className="absolute top-2 left-2 flex items-center gap-1.5">
          <span className="w-6 h-6 rounded-full bg-[#0d1117]/80 backdrop-blur-sm border border-[#30363d] flex items-center justify-center text-xs font-bold text-[#8b949e]">
            {rank}
          </span>
          {valueBadge && (
            <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${valueBadge.color}`}>{valueBadge.label}</span>
          )}
        </div>
        <div className="absolute top-2 right-2">
          <div className="flex flex-col items-end gap-1">
            <span className="px-2 py-0.5 rounded-full bg-[#0d1117]/80 backdrop-blur-sm border border-[#30363d] text-xs text-[#8b949e]">
              {product.source}
            </span>
            {product.listingType === "seller" && (
              <span className="px-2 py-0.5 rounded-full bg-[#1a7f37]/15 border border-[#1a7f37]/35 text-[11px] text-[#3fb950]">
                Seller Listing
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="p-4">
        <h3 className="text-[#e6edf3] text-sm font-semibold leading-snug mb-2 line-clamp-2 group-hover:text-white transition-colors duration-200">
          {product.name}
        </h3>

        <div className="space-y-1.5 mb-3">
          <div className="flex items-center gap-2">
            <span className="text-[11px] uppercase tracking-wide text-[#6e7681] min-w-[42px]">Source</span>
            <div className="flex items-center gap-0.5">{renderStars(ratingValue)}</div>
            <span className="text-[#f0a500] text-xs font-semibold">{hasRating ? displaySourceRating!.toFixed(1) : "N/A"}</span>
            <span className="text-[#484f58] text-xs">({displaySourceReviewCount == null ? "N/A" : displaySourceReviewCount.toLocaleString()})</span>
            {displaySourceRatingKind === "predicted" && (
              <span className="text-[10px] text-[#8b949e] border border-[#30363d] rounded px-1 py-0.5">Pred.</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] uppercase tracking-wide text-[#6e7681] min-w-[42px]">In App</span>
            <div className="flex items-center gap-0.5">{renderStars(appRating ?? 0)}</div>
            <span className="text-[#58a6ff] text-xs font-semibold">{hasAppRating ? appRating!.toFixed(1) : "N/A"}</span>
            <span className="text-[#484f58] text-xs">({appReviewCount == null ? "0" : appReviewCount.toLocaleString()})</span>
          </div>
        </div>

        <div className="flex items-center justify-between mb-3">
          <div>
            <span className="text-[#3fb950] text-lg font-bold">{renderPrice(product.price)}</span>
            {product.originalPrice && (
              <span className="text-[#484f58] text-xs line-through ml-2">PKR {product.originalPrice.toLocaleString()}</span>
            )}
            {priceRange && <p className="text-[11px] text-[#8b949e] mt-1">Range: {priceRange}</p>}
          </div>
          {discountPct != null && (
            <span className="flex items-center gap-1 text-xs text-[#3fb950] bg-[#1a7f37]/10 border border-[#1a7f37]/30 px-2 py-0.5 rounded-full">
              <TrendingUp size={10} />
              {discountPct}% off
            </span>
          )}
        </div>

        {product.reason && <p className="text-xs text-[#8b949e] mb-3 line-clamp-2">{product.reason}</p>}
        {product.storeName && product.listingType === "seller" && (
          <p className="text-xs text-[#58a6ff] mb-3 truncate">Sold by {product.storeName}</p>
        )}
        {(product.unitsSold != null || product.predictedDemandScore != null || (product.bestMonthLabels && product.bestMonthLabels.length > 0)) && (
          <div className="mb-3 space-y-1">
            {product.unitsSold != null && (
              <p className="text-[11px] text-[#8b949e]">
                Sold locally: <span className="text-[#c9d1d9]">{product.unitsSold.toLocaleString()}</span>
                {product.orderCount != null ? ` orders: ${product.orderCount.toLocaleString()}` : ""}
              </p>
            )}
            {product.predictedDemandScore != null && (
              <p className="text-[11px] text-[#8b949e]">
                Demand score: <span className="text-[#c9d1d9]">{product.predictedDemandScore.toFixed(2)}</span>
              </p>
            )}
            {product.bestMonthLabels && product.bestMonthLabels.length > 0 && (
              <p className="text-[11px] text-[#8b949e]">
                Best months: <span className="text-[#c9d1d9]">{product.bestMonthLabels.join(", ")}</span>
              </p>
            )}
          </div>
        )}

        <div className="flex items-center gap-2">
          {isInternal ? (
            <Link
              to={targetUrl}
              className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-[#58a6ff] text-[#0d1117] text-xs font-semibold hover:bg-[#79b8ff] hover:scale-105 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff] focus-visible:ring-offset-2 focus-visible:ring-offset-[#161b22]"
              aria-label={`Open ${product.name} details`}
            >
              <ShoppingCart size={12} />
              View Product
            </Link>
          ) : (
            <a
              href={targetUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-[#58a6ff] text-[#0d1117] text-xs font-semibold hover:bg-[#79b8ff] hover:scale-105 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff] focus-visible:ring-offset-2 focus-visible:ring-offset-[#161b22]"
              aria-label={`View ${product.name} on ${product.source}`}
            >
              <ShoppingCart size={12} />
              View Deal
            </a>
          )}
          {isInternal ? (
            <Link
              to={targetUrl}
              className="p-2 rounded-lg border border-[#30363d] text-[#8b949e] hover:text-white hover:border-[#58a6ff]/50 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff]"
              aria-label={`Open ${product.name}`}
            >
              <ExternalLink size={14} />
            </Link>
          ) : (
            <a
              href={targetUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="p-2 rounded-lg border border-[#30363d] text-[#8b949e] hover:text-white hover:border-[#58a6ff]/50 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff]"
              aria-label={`Open ${product.name} in new tab`}
            >
              <ExternalLink size={14} />
            </a>
          )}
        </div>
      </div>
    </motion.article>
  );
};

export default ProductCard;
