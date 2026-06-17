import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Bot, ImageOff, RotateCcw, Sparkles } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";

import ChatInput from "../components/ChatInput";
import Header from "../components/Header";
import MessageBubble from "../components/MessageBubble";
import ProductPanel from "../components/ProductPanel";
import QuickPrompts from "../components/QuickPrompts";
import ToolTrace from "../components/ToolTrace";
import TypingIndicator from "../components/TypingIndicator";
import {
  callAssistant,
  fetchConversationHistory,
  fetchSearchStatus,
  getDefaultTopK,
} from "../lib/api";
import { getOrCreateUserId, upsertRecentSession } from "../lib/sessionStore";
import type { AssistantContext, AssistantOffer, AssistantToolCall } from "../types/api";
import type { Message, Product, SearchLifecycle, ToolTrace as ToolTraceType } from "../types/chat";

const WELCOME_MESSAGE =
  "Hi. I am E-Assistant. Ask for any product and I will search local data and live web sources to find high-rating, low-price options.";

function formatPkr(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) {
    return "N/A";
  }
  return `PKR ${value.toLocaleString()}`;
}

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
  if (raw.startsWith("http://") || raw.startsWith("https://")) {
    return raw;
  }
  return null;
}

function sourceFallbackImageUrl(link: string | null | undefined, source: string | null | undefined): string | null {
  let host = "";
  if (link) {
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

function mapOfferToProduct(offer: AssistantOffer, index: number): Product {
  const total = offer.total_price_pkr ?? offer.price_pkr ?? null;
  const numericPrice = typeof total === "number" && Number.isFinite(total) ? total : null;
  const displayRating =
    typeof offer.display_source_rating === "number" && Number.isFinite(offer.display_source_rating)
      ? offer.display_source_rating
      : typeof offer.rating === "number" && Number.isFinite(offer.rating)
      ? offer.rating
      : null;
  const reviews =
    typeof offer.display_source_review_count === "number" && Number.isFinite(offer.display_source_review_count)
      ? Math.max(0, offer.display_source_review_count)
      : typeof offer.review_count === "number" && Number.isFinite(offer.review_count)
      ? Math.max(0, offer.review_count)
      : null;
  return {
    id: `${offer.link || offer.title || "offer"}_${index}`,
    name: offer.title || "Untitled offer",
    image: normalizeImageUrl(offer.image),
    fallbackImage: sourceFallbackImageUrl(offer.link, offer.source),
    price: numericPrice,
    rating: displayRating,
    reviewCount: reviews,
    sourceRating: typeof offer.source_rating === "number" && Number.isFinite(offer.source_rating) ? offer.source_rating : displayRating,
    sourceReviewCount:
      typeof offer.source_review_count === "number" && Number.isFinite(offer.source_review_count)
        ? Math.max(0, offer.source_review_count)
        : reviews,
    displaySourceRating: displayRating,
    displaySourceReviewCount: reviews,
    displaySourceRatingKind: offer.display_source_rating_kind ?? null,
    priceRangeMin:
      typeof offer.price_range_pkr_min === "number" && Number.isFinite(offer.price_range_pkr_min)
        ? offer.price_range_pkr_min
        : null,
    priceRangeMax:
      typeof offer.price_range_pkr_max === "number" && Number.isFinite(offer.price_range_pkr_max)
        ? offer.price_range_pkr_max
        : null,
    appRating: typeof offer.app_rating === "number" && Number.isFinite(offer.app_rating) ? offer.app_rating : null,
    appReviewCount:
      typeof offer.app_review_count === "number" && Number.isFinite(offer.app_review_count)
        ? Math.max(0, offer.app_review_count)
        : null,
    source: (offer.source || "web").toUpperCase(),
    url: offer.internal_path || offer.external_url || offer.link || "#",
    listingType: offer.listing_type || undefined,
    internalPath: offer.internal_path || null,
    externalUrl: offer.external_url || null,
    sellerId: offer.seller_id || null,
    sellerName: offer.seller_name || null,
    storeName: offer.store_name || null,
    reason: offer.reason || undefined,
  };
}

function mapToolType(name: string): "database" | "web" | "ranking" {
  const n = name.toLowerCase();
  if (n.includes("web") || n.includes("inspect")) {
    return "web";
  }
  if (n.includes("rank") || n.includes("cluster")) {
    return "ranking";
  }
  return "database";
}

function mapTraceRows(rows: AssistantToolCall[] | undefined): ToolTraceType[] {
  if (!rows || rows.length === 0) {
    return [];
  }
  return rows.map((row) => {
    const tool = String(row.tool_name || "tool");
    const output = row.output || {};
    const ok = output && typeof output === "object" ? (output as { ok?: unknown }).ok : undefined;
    const error = output && typeof output === "object" ? (output as { error?: unknown }).error : undefined;
    return {
      tool,
      type: mapToolType(tool),
      description: error ? `Error: ${String(error)}` : "Tool call completed.",
      status: ok === false || Boolean(error) ? "error" : "success",
    };
  });
}

function normalizeAssistantContext(value: unknown): AssistantContext | undefined {
  if (!value || typeof value !== "object") {
    return undefined;
  }
  return value as AssistantContext;
}

function normalizeOfferArray(value: unknown): AssistantOffer[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((row): row is AssistantOffer => Boolean(row && typeof row === "object"));
}

const Home: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const userId = useMemo(() => getOrCreateUserId(), []);

  const [messages, setMessages] = useState<Message[]>([
    {
      id: "welcome",
      role: "assistant",
      content: WELCOME_MESSAGE,
      timestamp: Date.now(),
    },
  ]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [products, setProducts] = useState<Product[]>([]);
  const [brokenImageIds, setBrokenImageIds] = useState<Record<string, boolean>>({});
  const [currentQuery, setCurrentQuery] = useState("");
  const [conversationId, setConversationId] = useState<string>("");
  const [filters, setFilters] = useState({ minRating: 0, maxPrice: 500000 });
  const [showPanel, setShowPanel] = useState(false);
  const [productPanelWidth, setProductPanelWidth] = useState(45);
  const [assistantContext, setAssistantContext] = useState<AssistantContext | undefined>(undefined);
  const [searchLifecycle, setSearchLifecycle] = useState<SearchLifecycle>({
    phase: "idle",
    status: "idle",
    pendingOnlineRefresh: false,
  });

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const autoRunQueryRef = useRef<string>("");
  const lastAutoSubmitKeyRef = useRef<string>("");
  const lastCompletionNoticeRef = useRef<string>("");
  const hydratedConversationRef = useRef<string>("");
  const searchStatusHydratedRef = useRef<string>("");
  const productLayoutStyle = useMemo(
    () => ({ "--product-panel-width": `${productPanelWidth}%` } as React.CSSProperties),
    [productPanelWidth]
  );

  const applySearchStatus = useCallback(
    (
      status: {
        query?: string;
        search_phase: "local_partial" | "complete";
        search_status: "local_ready" | "online_searching" | "complete";
        pending_online_refresh: boolean;
        merged_results: AssistantOffer[];
        notice?: string | null;
        report_id?: string | null;
      },
      options?: { appendCompletionMessage?: boolean }
    ) => {
      const mergedProducts = (status.merged_results || []).map(mapOfferToProduct);
      setProducts(mergedProducts);
      setShowPanel(mergedProducts.length > 0 || status.pending_online_refresh);
      setCurrentQuery(status.query || currentQuery);
      setSearchLifecycle({
        phase: status.search_phase,
        status: status.search_status,
        pendingOnlineRefresh: status.pending_online_refresh,
        notice: status.notice || undefined,
      });
      if (status.report_id) {
        setAssistantContext((prev) => ({ ...(prev || {}), report_id: status.report_id }));
      }
      const shouldAppendCompletion =
        options?.appendCompletionMessage &&
        status.search_status === "complete" &&
        Boolean(status.notice) &&
        lastCompletionNoticeRef.current !== status.notice;
      if (shouldAppendCompletion) {
        lastCompletionNoticeRef.current = status.notice || "";
        setMessages((prev) => [
          ...prev,
          {
            id: `a_status_${Date.now()}`,
            role: "assistant",
            content: String(status.notice || "Online search finished."),
            timestamp: Date.now(),
            products: mergedProducts,
            assistantContext: {
              ...(assistantContext || {}),
              summary: "Updated results after online search enrichment.",
            },
          },
        ]);
      }
    },
    [assistantContext, currentQuery]
  );

  const handlePanelResizeStart = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const container = event.currentTarget.parentElement;
    if (!container) {
      return;
    }
    const rect = container.getBoundingClientRect();
    const onPointerMove = (moveEvent: PointerEvent) => {
      const nextWidth = ((rect.right - moveEvent.clientX) / rect.width) * 100;
      setProductPanelWidth(Math.min(68, Math.max(28, nextWidth)));
    };
    const stopResize = () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", stopResize);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", stopResize);
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  useEffect(() => {
    const id = (searchParams.get("conversation") || "").trim();
    if (!id) {
      hydratedConversationRef.current = "";
      const presetQuery = (searchParams.get("q") || "").trim();
      const presetProductId = (searchParams.get("product") || "").trim();
      const presetKey = `${presetProductId}::${presetQuery}`;
      if (presetQuery && autoRunQueryRef.current !== presetKey) {
        autoRunQueryRef.current = presetKey;
        setInput(presetQuery);
      }
      return;
    }
    if (hydratedConversationRef.current === id) {
      return;
    }
    let active = true;
    const run = async () => {
      try {
        const history = await fetchConversationHistory({
          conversationId: id,
          userId,
          limit: 200,
        });
        if (!active) {
          return;
        }
        const mapped: Message[] = history.turns
          .filter((x) => x.role === "user" || x.role === "assistant")
          .map((row) => ({
            id: `hist_${row.seq}`,
            role: row.role,
            content: row.content || "",
            timestamp: row.ts ? Date.parse(row.ts) : Date.now(),
            assistantContext:
              row.role === "assistant" ? normalizeAssistantContext((row.metadata || {}).assistant_context) : undefined,
          }));
        setMessages(
          mapped.length > 0
            ? mapped
            : [
                {
                  id: "welcome_restored",
                  role: "assistant",
                  content: WELCOME_MESSAGE,
                  timestamp: Date.now(),
                },
              ]
        );
        setConversationId(history.conversation_id || id);
        hydratedConversationRef.current = history.conversation_id || id;
        const lastAssistantTurn = [...history.turns]
          .reverse()
          .find((row) => row.role === "assistant");
        const restoredContext = normalizeAssistantContext((lastAssistantTurn?.metadata || {}).assistant_context);
        const restoredOffers = normalizeOfferArray((lastAssistantTurn?.metadata || {}).results_preview);
        if (restoredContext) {
          setAssistantContext(restoredContext);
          if (restoredContext.results_query) {
            setCurrentQuery(restoredContext.results_query);
          }
        } else {
          setAssistantContext(undefined);
        }
        if (restoredOffers.length > 0) {
          const restoredProducts = restoredOffers.map(mapOfferToProduct);
          setProducts(restoredProducts);
          setShowPanel(true);
        } else {
          setProducts([]);
          setShowPanel(false);
        }
        try {
          const status = await fetchSearchStatus({ conversationId: history.conversation_id || id, userId });
          if (!active) {
            return;
          }
          applySearchStatus(status, { appendCompletionMessage: false });
        } catch {
          // Keep restored message history even if phased search state is absent.
        }
      } catch (error) {
        if (!active) {
          return;
        }
        if (messages.length <= 1) {
          setMessages((prev) => [
            ...prev,
            {
              id: `err_load_${Date.now()}`,
              role: "assistant",
              content: `Could not load conversation ${id}: ${error instanceof Error ? error.message : "unknown error"}`,
              timestamp: Date.now(),
            },
          ]);
        }
      }
    };
    run();
    return () => {
      active = false;
    };
  }, [applySearchStatus, messages.length, searchParams, userId]);

  const handleSubmit = useCallback(async (options?: { queryOverride?: string; referenceProductId?: string }) => {
    const query = (options?.queryOverride ?? input).trim();
    if (!query || isLoading) {
      return;
    }
    const userMsg: Message = {
      id: `u_${Date.now()}`,
      role: "user",
      content: query,
      timestamp: Date.now(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setIsLoading(true);
    setCurrentQuery(query);
    try {
      const response = await callAssistant({
        query,
        conversationId: conversationId || undefined,
        userId,
        referenceProductId: options?.referenceProductId,
        topK: getDefaultTopK(),
        includeToolTrace: true,
      });
      const nextConversationId = response.conversation_id || conversationId;
      const mappedProducts = (response.results || []).map(mapOfferToProduct);
      const mappedTraces = mapTraceRows(response.tool_calls);
      const nextContext = response.assistant_context;
      const assistantMsg: Message = {
        id: `a_${Date.now()}`,
        role: "assistant",
        content: response.answer || "No response generated.",
        timestamp: Date.now(),
        products: mappedProducts,
        traces: mappedTraces,
        assistantContext: nextContext,
      };
      setMessages((prev) => [...prev, assistantMsg]);
      setBrokenImageIds({});
      setProducts(mappedProducts);
      setShowPanel(mappedProducts.length > 0 || Boolean(response.pending_online_refresh));
      setAssistantContext(nextContext);
      setCurrentQuery(nextContext?.results_query || query);
      setSearchLifecycle({
        phase: response.search_phase || "complete",
        status: response.search_status || "complete",
        pendingOnlineRefresh: Boolean(response.pending_online_refresh),
        notice: undefined,
      });
      lastCompletionNoticeRef.current = "";
      if (nextConversationId) {
        setConversationId(nextConversationId);
        hydratedConversationRef.current = nextConversationId;
        setSearchParams((prev) => {
          const p = new URLSearchParams(prev);
          p.set("conversation", nextConversationId);
          p.delete("q");
          p.delete("product");
          return p;
        });
        upsertRecentSession({
          conversationId: nextConversationId,
          query,
          answer: response.answer || "",
          resultCount: mappedProducts.length,
          timestamp: Date.now(),
        });
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Request failed.";
      setMessages((prev) => [
        ...prev,
        {
          id: `a_err_${Date.now()}`,
          role: "assistant",
          content: `Request failed: ${msg}`,
          timestamp: Date.now(),
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  }, [input, isLoading, userId, conversationId, setSearchParams]);

  useEffect(() => {
    if (!conversationId || !searchLifecycle.pendingOnlineRefresh) {
      return;
    }
    let active = true;
    const timer = window.setInterval(async () => {
      try {
        const status = await fetchSearchStatus({ conversationId, userId });
        if (!active) {
          return;
        }
        applySearchStatus(status, { appendCompletionMessage: true });
        if (!status.pending_online_refresh) {
          window.clearInterval(timer);
        }
      } catch {
        if (active) {
          setSearchLifecycle((prev) => ({
            ...prev,
            phase: "complete",
            status: "complete",
            pendingOnlineRefresh: false,
            notice: "Online search refresh failed. Local results are still available.",
          }));
        }
        window.clearInterval(timer);
      }
    }, 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [applySearchStatus, conversationId, searchLifecycle.pendingOnlineRefresh, userId]);

  useEffect(() => {
    if (!conversationId || searchStatusHydratedRef.current === conversationId) {
      return;
    }
    let active = true;
    const run = async () => {
      try {
        const status = await fetchSearchStatus({ conversationId, userId });
        if (!active) {
          return;
        }
        applySearchStatus(status, { appendCompletionMessage: false });
        searchStatusHydratedRef.current = conversationId;
      } catch {
        // Ignore missing phased state; conversation text is already restored.
      }
    };
    void run();
    return () => {
      active = false;
    };
  }, [applySearchStatus, conversationId, userId]);

  useEffect(() => {
    const presetQuery = (searchParams.get("q") || "").trim();
    const presetProductId = (searchParams.get("product") || "").trim();
    const conversation = (searchParams.get("conversation") || "").trim();
    const presetKey = `${presetProductId}::${presetQuery}`;
    if (!presetQuery || conversation || isLoading || input !== presetQuery) {
      return;
    }
    if (lastAutoSubmitKeyRef.current === presetKey) {
      return;
    }
    lastAutoSubmitKeyRef.current = presetKey;
    void handleSubmit({
      queryOverride: presetQuery,
      referenceProductId: presetProductId || undefined,
    });
  }, [handleSubmit, input, isLoading, messages, searchParams]);

  const handleNewSearch = useCallback(() => {
    setMessages([
      {
        id: `welcome_${Date.now()}`,
        role: "assistant",
        content: "Ready for a new search.",
        timestamp: Date.now(),
      },
    ]);
    setProducts([]);
    setBrokenImageIds({});
    setCurrentQuery("");
    setInput("");
    setShowPanel(false);
    setConversationId("");
    setAssistantContext(undefined);
    searchStatusHydratedRef.current = "";
    autoRunQueryRef.current = "";
    lastAutoSubmitKeyRef.current = "";
    setSearchLifecycle({
      phase: "idle",
      status: "idle",
      pendingOnlineRefresh: false,
    });
    lastCompletionNoticeRef.current = "";
    setSearchParams((prev) => {
      const p = new URLSearchParams(prev);
      p.delete("conversation");
      p.delete("q");
      p.delete("product");
      return p;
    });
  }, [setSearchParams]);

  return (
    <div className="h-screen bg-[#0d1117] flex flex-col overflow-hidden">
      <Header />
      <main className="min-h-0 flex-1 flex flex-col pt-16" id="main-content">
        <div className="flex-shrink-0 border-b border-[#30363d] bg-[#0d1117]">
          <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-[#3fb950] animate-pulse" />
                <span className="text-xs text-[#8b949e]">Agent online | Groq tool-calling active</span>
              </div>
              <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[#161b22] border border-[#30363d]">
                <Sparkles size={10} className="text-[#f0a500]" />
                <span className="text-xs text-[#8b949e]">
                  Sources: iShopping | Shophive | Daraz | Live web search
                </span>
              </div>
              {assistantContext?.mode_label && (
                <div className="hidden md:flex items-center gap-2 px-2.5 py-1 rounded-full bg-[#161b22] border border-[#30363d] text-xs text-[#8b949e] min-w-0">
                  <span className="text-[#58a6ff] font-semibold whitespace-nowrap">{assistantContext.mode_label}</span>
                  {assistantContext.response_focus && assistantContext.response_focus !== "general" && (
                    <span className="px-1.5 py-0.5 rounded-full border border-[#30363d] text-[#c9d1d9] capitalize">
                      {assistantContext.response_focus}
                    </span>
                  )}
                  {assistantContext.selected_offer?.title && (
                    <span className="truncate max-w-[280px] text-[#c9d1d9]">{assistantContext.selected_offer.title}</span>
                  )}
                  {!assistantContext.selected_offer?.title &&
                    assistantContext.comparison_offers &&
                    assistantContext.comparison_offers.length > 1 && (
                      <span className="truncate max-w-[320px] text-[#c9d1d9]">
                        {assistantContext.comparison_offers
                          .map((offer) => offer.title || "product")
                          .filter(Boolean)
                          .slice(0, 2)
                          .join(" vs ")}
                      </span>
                    )}
                </div>
              )}
            </div>
            <button
              onClick={handleNewSearch}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#30363d] text-xs text-[#8b949e] hover:text-white hover:border-[#58a6ff]/40 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff]"
              aria-label="Start new search"
            >
              <RotateCcw size={12} />
              New Search
            </button>
          </div>
        </div>

        <div className="product-layout min-h-0 flex-1 flex overflow-hidden max-w-7xl w-full mx-auto" style={productLayoutStyle}>
          <div className={`min-h-0 flex flex-col transition-all duration-500 ${showPanel ? "chat-pane-with-products" : "w-full"} border-r border-[#30363d]`}>
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6 space-y-5" role="log" aria-label="Conversation history" aria-live="polite">
              {messages.length === 1 && !isLoading && (
                <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="flex flex-col items-center justify-center py-8 text-center">
                  <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-[#58a6ff]/20 to-[#7c3aed]/20 border border-[#58a6ff]/20 flex items-center justify-center mb-4">
                    <Bot size={28} className="text-[#58a6ff]" />
                  </div>
                  <h1 className="font-heading text-2xl font-bold text-white mb-2">E-Assistant Product Search</h1>
                  <p className="text-[#8b949e] text-sm max-w-sm leading-relaxed">
                    Ask naturally, for example: "best smartwatch under 15000 with high rating".
                  </p>
                </motion.div>
              )}

              {messages.map((msg) => (
                <div key={msg.id} className="space-y-3">
                  <MessageBubble message={msg} />
                  {msg.traces && msg.traces.length > 0 && (
                    <div className="ml-11">
                      <ToolTrace traces={msg.traces} />
                    </div>
                  )}
                </div>
              ))}

              <AnimatePresence>{isLoading && <TypingIndicator />}</AnimatePresence>
              <div ref={messagesEndRef} />
            </div>

            <div className="sticky bottom-0 flex-shrink-0 border-t border-[#30363d] px-4 py-4 space-y-3 bg-[#0d1117]">
              {messages.length <= 1 && <QuickPrompts onSelect={setInput} disabled={isLoading} />}
              <ChatInput
                value={input}
                onChange={setInput}
                onSubmit={handleSubmit}
                disabled={isLoading}
                placeholder="e.g. Find best Samsung phone under 150000 with rating above 4.0"
              />
              <p className="text-xs text-[#484f58] text-center">
                Conversation: {conversationId || "new"} | User: {userId}
              </p>
            </div>
          </div>

          {showPanel && (
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize product results panel"
              onPointerDown={handlePanelResizeStart}
              className="hidden lg:flex w-1 cursor-col-resize items-center justify-center bg-[#30363d]/70 hover:bg-[#58a6ff] focus:outline-none focus-visible:bg-[#58a6ff]"
              tabIndex={0}
            >
              <div className="h-12 w-px rounded-full bg-[#8b949e]/60" />
            </div>
          )}

          <AnimatePresence>
            {showPanel && (
              <motion.div
                initial={{ opacity: 0, x: 40 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 40 }}
                transition={{ duration: 0.35, ease: "easeOut" }}
                className="product-pane-resizable hidden lg:flex flex-col overflow-hidden"
              >
                <ProductPanel
                  products={products}
                  query={currentQuery}
                  assistantContext={assistantContext}
                  searchLifecycle={searchLifecycle}
                  onClearResults={() => {
                    setProducts([]);
                    setShowPanel(false);
                  }}
                  filters={filters}
                  onFilterChange={setFilters}
                />
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <AnimatePresence>
          {showPanel && products.length > 0 && (
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 20 }} className="lg:hidden border-t border-[#30363d] bg-[#0d1117]">
              <div className="px-4 py-3 flex items-center justify-between border-b border-[#30363d]">
                <span className="text-sm font-semibold text-[#e6edf3]">Product Results ({products.length})</span>
                <button
                  onClick={() => setShowPanel(false)}
                  className="text-xs text-[#8b949e] hover:text-white transition-colors duration-200 focus:outline-none focus-visible:underline"
                >
                  Hide
                </button>
              </div>
              <div className="px-4 py-4 grid grid-cols-1 sm:grid-cols-2 gap-3 max-h-[60vh] overflow-y-auto">
                {products.map((product, i) => (
                  <div key={product.id}>
                    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.07 }}>
                      <div className="bg-[#161b22] border border-[#30363d] rounded-xl overflow-hidden hover:border-[#58a6ff]/40 transition-all duration-300">
                        {(product.image || product.fallbackImage) && !brokenImageIds[product.id] ? (
                          <img
                            src={product.image || product.fallbackImage || ""}
                            alt={product.name}
                            width={400}
                            height={160}
                            className={`w-full h-32 ${product.image ? "object-cover" : "object-contain p-8 bg-[#111827]"}`}
                            loading="lazy"
                            referrerPolicy="no-referrer"
                            onError={() => {
                              setBrokenImageIds((prev) => ({ ...prev, [product.id]: true }));
                            }}
                          />
                        ) : (
                          <div className="w-full h-32 bg-gradient-to-br from-[#1f2937] via-[#111827] to-[#0f172a] border-b border-[#30363d] flex items-center justify-center">
                            <div className="flex items-center gap-1.5 text-[#6e7681] text-xs">
                              <ImageOff size={12} />
                              No image
                            </div>
                          </div>
                        )}
                        <div className="p-3">
                          <p className="text-[#e6edf3] text-xs font-semibold line-clamp-2 mb-1.5">{product.name}</p>
                          <div className="flex items-center justify-between">
                            <span className="text-[#3fb950] font-bold text-sm">{formatPkr(product.price)}</span>
                            <span className="text-[#f0a500] text-xs">{product.rating == null ? "N/A" : `${product.rating.toFixed(1)}*`}</span>
                          </div>
                          {(product.internalPath || product.url.startsWith("/")) ? (
                            <Link
                              to={product.internalPath || product.url}
                              className="mt-2 block text-center px-3 py-1.5 rounded-lg bg-[#58a6ff] text-[#0d1117] text-xs font-semibold hover:bg-[#79b8ff] transition-colors duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff]"
                            >
                              View Product
                            </Link>
                          ) : (
                            <a
                              href={product.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="mt-2 block text-center px-3 py-1.5 rounded-lg bg-[#58a6ff] text-[#0d1117] text-xs font-semibold hover:bg-[#79b8ff] transition-colors duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff]"
                            >
                              View Deal
                            </a>
                          )}
                        </div>
                      </div>
                    </motion.div>
                  </div>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
  );
};

export default Home;
