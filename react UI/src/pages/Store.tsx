import React from "react";
import { motion } from "framer-motion";
import { Link, useSearchParams } from "react-router-dom";
import { Search, Store as StoreIcon } from "lucide-react";

import Footer from "../components/Footer";
import Header from "../components/Header";
import ProductCard from "../components/ProductCard";
import { fetchStoreCatalog } from "../lib/api";
import { mapStoreProductToCard } from "../lib/storeMapper";
import type { StoreCatalogResponse } from "../types/api";

const StorePage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const [catalog, setCatalog] = React.useState<StoreCatalogResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  const q = searchParams.get("q") || "";
  const category = searchParams.get("category") || "";
  const listingType = (searchParams.get("listing_type") || "all") as "all" | "scraped" | "seller";
  const sort = (searchParams.get("sort") || "newest") as "newest" | "relevance" | "price_asc" | "price_desc" | "rating";
  const page = Number(searchParams.get("page") || 1);

  const [draftQuery, setDraftQuery] = React.useState(q);

  React.useEffect(() => {
    setDraftQuery(q);
  }, [q]);

  React.useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    fetchStoreCatalog({
      q,
      category: category || undefined,
      listingType,
      sort,
      page,
      pageSize: 24,
    })
      .then((payload) => {
        if (!active) {
          return;
        }
        setCatalog(payload);
      })
      .catch((err) => {
        if (!active) {
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load store catalog.");
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [category, listingType, page, q, sort]);

  const updateParams = React.useCallback(
    (patch: Record<string, string | number | null | undefined>) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        for (const [key, value] of Object.entries(patch)) {
          if (value == null || value === "") {
            next.delete(key);
          } else {
            next.set(key, String(value));
          }
        }
        if (!("page" in patch)) {
          next.set("page", "1");
        }
        return next;
      });
    },
    [setSearchParams]
  );

  const products = React.useMemo(() => (catalog?.items || []).map(mapStoreProductToCard), [catalog]);

  return (
    <div className="min-h-screen bg-[#0d1117] flex flex-col">
      <Header />
      <main className="flex-1 pt-16">
        <div className="max-w-7xl mx-auto px-6 py-10">
          <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}>
            <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-6 mb-8">
              <div>
                <div className="flex items-center gap-3 mb-2">
                  <div className="w-10 h-10 rounded-xl bg-[#58a6ff]/10 border border-[#58a6ff]/20 flex items-center justify-center">
                    <StoreIcon size={20} className="text-[#58a6ff]" />
                  </div>
                  <h1 className="font-heading text-3xl font-bold text-white">Marketplace Store</h1>
                </div>
                <p className="text-sm text-[#8b949e] max-w-2xl">
                  Browse scraped marketplace offers already in the database and seller-managed listings on the platform.
                </p>
              </div>
              <div className="flex flex-wrap gap-3">
                <Link
                  to={q ? `/?q=${encodeURIComponent(q)}` : "/"}
                  className="px-4 py-2 rounded-lg border border-[#58a6ff]/30 bg-[#58a6ff]/10 text-[#58a6ff] text-sm font-medium hover:bg-[#58a6ff]/15 transition-all duration-200"
                >
                  Ask Chatbot About This Store
                </Link>
              </div>
            </div>

            <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-4 mb-8">
              <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,2fr)_repeat(3,minmax(0,1fr))] gap-3">
                <form
                  onSubmit={(event) => {
                    event.preventDefault();
                    updateParams({ q: draftQuery, page: 1 });
                  }}
                  className="relative"
                >
                  <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#6e7681]" />
                  <input
                    value={draftQuery}
                    onChange={(event) => setDraftQuery(event.target.value)}
                    placeholder="Search store products, brands, or categories"
                    className="w-full rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-10 py-3 outline-none focus:border-[#58a6ff]/50"
                  />
                </form>

                <select
                  value={listingType}
                  onChange={(event) => updateParams({ listing_type: event.target.value, page: 1 })}
                  className="rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-3 py-3 outline-none focus:border-[#58a6ff]/50"
                >
                  <option value="all">All Listings</option>
                  <option value="scraped">Scraped Marketplace</option>
                  <option value="seller">Seller Listings</option>
                </select>

                <select
                  value={category}
                  onChange={(event) => updateParams({ category: event.target.value, page: 1 })}
                  className="rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-3 py-3 outline-none focus:border-[#58a6ff]/50"
                >
                  <option value="">All Categories</option>
                  {(catalog?.categories || []).map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>

                <select
                  value={sort}
                  onChange={(event) => updateParams({ sort: event.target.value, page: 1 })}
                  className="rounded-lg bg-[#0d1117] border border-[#30363d] text-sm text-white px-3 py-3 outline-none focus:border-[#58a6ff]/50"
                >
                  <option value="newest">Newest</option>
                  <option value="relevance">Relevance</option>
                  <option value="price_asc">Price: Low to High</option>
                  <option value="price_desc">Price: High to Low</option>
                  <option value="rating">Highest Rated</option>
                </select>
              </div>
            </div>

            <div className="flex items-center justify-between mb-5">
              <div className="text-sm text-[#8b949e]">
                {loading ? "Loading catalog..." : `${catalog?.total || 0} products available`}
              </div>
              {catalog && (
                <div className="text-xs text-[#6e7681]">
                  Page {catalog.page} of {catalog.pages}
                </div>
              )}
            </div>

            {error && (
              <div className="mb-4 p-3 rounded-lg border border-[#f85149]/40 bg-[#f85149]/10 text-[#ffb3b3] text-sm">
                {error}
              </div>
            )}

            {loading ? (
              <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
                {Array.from({ length: 6 }).map((_, index) => (
                  <div key={index} className="h-[360px] rounded-xl bg-[#161b22] border border-[#30363d] animate-pulse" />
                ))}
              </div>
            ) : products.length > 0 ? (
              <>
                <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
                  {products.map((product, index) => (
                    <ProductCard key={product.id} product={product} rank={index + 1 + (page - 1) * 24} />
                  ))}
                </div>
                <div className="mt-8 flex items-center justify-center gap-3">
                  <button
                    disabled={page <= 1}
                    onClick={() => updateParams({ page: page - 1 })}
                    className="px-4 py-2 rounded-lg border border-[#30363d] text-sm text-[#c9d1d9] disabled:opacity-40"
                  >
                    Previous
                  </button>
                  <button
                    disabled={page >= (catalog?.pages || 1)}
                    onClick={() => updateParams({ page: page + 1 })}
                    className="px-4 py-2 rounded-lg border border-[#30363d] text-sm text-[#c9d1d9] disabled:opacity-40"
                  >
                    Next
                  </button>
                </div>
              </>
            ) : (
              <div className="p-8 rounded-xl border border-[#30363d] bg-[#161b22] text-center text-[#8b949e] text-sm">
                No products matched the current filters.
              </div>
            )}
          </motion.div>
        </div>
      </main>
      <Footer />
    </div>
  );
};

export default StorePage;
