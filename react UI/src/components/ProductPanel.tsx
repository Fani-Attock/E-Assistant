import React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Package, SlidersHorizontal, X } from "lucide-react";

import ProductCard from "./ProductCard";
import type { AssistantContext } from "../types/api";
import type { Product, SearchLifecycle } from "../types/chat";

interface ProductPanelProps {
  products: Product[];
  query: string;
  assistantContext?: AssistantContext;
  searchLifecycle?: SearchLifecycle;
  onClearResults: () => void;
  filters: { minRating: number; maxPrice: number };
  onFilterChange: (filters: { minRating: number; maxPrice: number }) => void;
}

const ProductPanel: React.FC<ProductPanelProps> = ({
  products,
  query,
  assistantContext,
  searchLifecycle,
  onClearResults,
  filters,
  onFilterChange,
}) => {
  const [showFilters, setShowFilters] = React.useState(false);

  const filtered = products.filter((p) => {
    const rating = p.rating ?? 0;
    const price = p.price;
    const passRating = rating >= filters.minRating;
    const passPrice = price == null ? true : price <= filters.maxPrice;
    return passRating && passPrice;
  });

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      <div className="flex-shrink-0 px-4 py-3 border-b border-[#30363d] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Package size={16} className="text-[#58a6ff]" />
          <span className="text-sm font-semibold text-[#e6edf3]">
            Results
            {filtered.length > 0 && <span className="ml-2 text-xs text-[#8b949e] font-normal">{filtered.length} found</span>}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowFilters((v) => !v)}
            className={`p-1.5 rounded-lg border transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff] ${
              showFilters
                ? "border-[#58a6ff]/50 bg-[#58a6ff]/10 text-[#58a6ff]"
                : "border-[#30363d] text-[#8b949e] hover:text-white hover:border-[#58a6ff]/30"
            }`}
            aria-label="Toggle filters"
            aria-expanded={showFilters}
          >
            <SlidersHorizontal size={14} />
          </button>
          {products.length > 0 && (
            <button
              onClick={onClearResults}
              className="p-1.5 rounded-lg border border-[#30363d] text-[#8b949e] hover:text-white hover:border-[#484f58] transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff]"
              aria-label="Clear results"
            >
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      <AnimatePresence>
        {showFilters && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden border-b border-[#30363d] bg-[#161b22]/50"
          >
            <div className="px-4 py-3 space-y-3">
              <div>
                <label className="text-xs text-[#8b949e] font-medium block mb-1.5">
                  Min Rating: {filters.minRating.toFixed(1)}*
                </label>
                <input
                  type="range"
                  min={0}
                  max={5}
                  step={0.5}
                  value={filters.minRating}
                  onChange={(e) => onFilterChange({ ...filters, minRating: parseFloat(e.target.value) })}
                  className="w-full accent-[#58a6ff] cursor-pointer"
                  aria-label="Minimum rating filter"
                />
              </div>
              <div>
                <label className="text-xs text-[#8b949e] font-medium block mb-1.5">
                  Max Price: PKR {filters.maxPrice.toLocaleString()}
                </label>
                <input
                  type="range"
                  min={1000}
                  max={1000000}
                  step={1000}
                  value={filters.maxPrice}
                  onChange={(e) => onFilterChange({ ...filters, maxPrice: parseInt(e.target.value, 10) })}
                  className="w-full accent-[#58a6ff] cursor-pointer"
                  aria-label="Maximum price filter"
                />
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {query && (
        <div className="flex-shrink-0 px-4 py-2 border-b border-[#30363d]/50">
          <p className="text-xs text-[#484f58] truncate">
            Showing results for: <span className="text-[#8b949e] italic">"{query}"</span>
          </p>
        </div>
      )}

      {assistantContext && (
        <div className="flex-shrink-0 px-4 py-3 border-b border-[#30363d]/50 bg-[#11161d]">
          <div className="flex flex-wrap items-center gap-2 mb-1.5">
            {assistantContext.mode_label && (
              <span className="text-[11px] font-semibold text-[#58a6ff] px-2 py-1 rounded-md border border-[#58a6ff]/30 bg-[#58a6ff]/10">
                {assistantContext.mode_label}
              </span>
            )}
            {assistantContext.response_focus && assistantContext.response_focus !== "general" && (
              <span className="text-[11px] text-[#c9d1d9] px-2 py-1 rounded-md border border-[#30363d] capitalize">
                {assistantContext.response_focus}
              </span>
            )}
            {assistantContext.selected_offer?.title && (
              <span className="text-[11px] text-[#8b949e] px-2 py-1 rounded-md border border-[#30363d] max-w-full truncate">
                {assistantContext.selected_offer.title}
              </span>
            )}
          </div>
          {assistantContext.comparison_offers && assistantContext.comparison_offers.length > 1 && (
            <p className="text-xs text-[#8b949e] truncate mb-1">
              Comparing:{" "}
              <span className="text-[#c9d1d9]">
                {assistantContext.comparison_offers
                  .map((offer) => offer.title || "product")
                  .filter(Boolean)
                  .slice(0, 2)
                  .join(" vs ")}
              </span>
            </p>
          )}
          {assistantContext.summary && <p className="text-xs text-[#8b949e] leading-relaxed">{assistantContext.summary}</p>}
          {assistantContext.decision_reason && (
            <p className="text-xs text-[#6e7681] leading-relaxed mt-1">{assistantContext.decision_reason}</p>
          )}
        </div>
      )}

      {searchLifecycle && searchLifecycle.status !== "idle" && (
        <div className="flex-shrink-0 px-4 py-3 border-b border-[#30363d]/50 bg-[#0f1720]">
          <div className="flex items-center gap-2">
            <span
              className={`inline-flex h-2.5 w-2.5 rounded-full ${
                searchLifecycle.pendingOnlineRefresh ? "bg-[#f0a500] animate-pulse" : "bg-[#3fb950]"
              }`}
            />
            <p className="text-xs text-[#8b949e]">
              {searchLifecycle.pendingOnlineRefresh
                ? "Local results are ready. Searching online for more offers."
                : "Search is complete."}
            </p>
          </div>
          {searchLifecycle.notice && <p className="text-xs text-[#6e7681] mt-1 leading-relaxed">{searchLifecycle.notice}</p>}
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3" role="list" aria-label="Product results">
        <AnimatePresence mode="popLayout">
          {filtered.length > 0 ? (
            filtered.map((product, i) => (
              <div key={product.id} role="listitem">
                <ProductCard product={product} rank={i + 1} delay={i * 0.08} />
              </div>
            ))
          ) : products.length > 0 ? (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-col items-center justify-center py-12 text-center">
              <SlidersHorizontal size={32} className="text-[#30363d] mb-3" />
              <p className="text-[#8b949e] text-sm font-medium">No products match your filters</p>
              <p className="text-[#484f58] text-xs mt-1">Try adjusting the rating or price range</p>
            </motion.div>
          ) : (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-col items-center justify-center py-16 text-center">
              <div className="w-16 h-16 rounded-2xl bg-[#161b22] border border-[#30363d] flex items-center justify-center mb-4">
                <Package size={28} className="text-[#30363d]" />
              </div>
              <p className="text-[#8b949e] text-sm font-medium">No results yet</p>
              <p className="text-[#484f58] text-xs mt-1 max-w-[180px]">Ask the AI to search for products to see results here</p>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
};

export default ProductPanel;
