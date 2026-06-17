import type {
  AssistantHistoryResponse,
  AssistantResponse,
  DeleteConversationResponse,
  MarketplaceAuthResponse,
  MarketplaceMeResponse,
  ProductReviewsResponse,
  SavedReportDetailResponse,
  SavedReportsResponse,
  SearchStatusResponse,
  SellerProductsResponse,
  SellerProfileResponse,
  StoreCatalogResponse,
  StoreProductDetailResponse,
  StoreProduct,
} from "../types/api";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() || "/api";
const SERVICE_API_KEY = (import.meta.env.VITE_SERVICE_API_KEY as string | undefined)?.trim() || "";
const DEFAULT_TOP_K = Number(import.meta.env.VITE_DEFAULT_TOP_K || 5);

function withBase(path: string): string {
  const base = API_BASE_URL.replace(/\/+$/, "");
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${base}${p}`;
}

function defaultHeaders(): HeadersInit {
  const headers: HeadersInit = {
    "Content-Type": "application/json",
  };
  if (SERVICE_API_KEY) {
    headers["X-API-Key"] = SERVICE_API_KEY;
  }
  return headers;
}

function authHeaders(token?: string, includeJson = true): HeadersInit {
  const headers: HeadersInit = includeJson ? { "Content-Type": "application/json" } : {};
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

async function parseJson<T>(response: Response): Promise<T> {
  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { detail: text || "Invalid JSON response from server." };
  }
  if (!response.ok) {
    let detail = response.statusText || "Request failed.";
    if (typeof payload === "object" && payload !== null && "detail" in payload) {
      const rawDetail = (payload as { detail?: unknown }).detail;
      if (typeof rawDetail === "string") {
        detail = rawDetail;
      } else if (rawDetail && typeof rawDetail === "object") {
        const structured = rawDetail as { message?: unknown; field_errors?: Array<{ field?: unknown; message?: unknown }> };
        const fieldErrors = Array.isArray(structured.field_errors)
          ? structured.field_errors
              .map((item) => {
                const field = typeof item?.field === "string" ? item.field : "field";
                const message = typeof item?.message === "string" ? item.message : "Invalid value";
                return `${field}: ${message}`;
              })
              .filter(Boolean)
          : [];
        detail =
          (typeof structured.message === "string" && structured.message) ||
          fieldErrors.join(" | ") ||
          "Validation failed.";
      }
    }
    throw new Error(detail);
  }
  return payload as T;
}

export function getDefaultTopK(): number {
  const value = Number.isFinite(DEFAULT_TOP_K) ? DEFAULT_TOP_K : 5;
  return Math.max(1, Math.min(20, value));
}

export async function callAssistant(params: {
  query: string;
  conversationId?: string;
  userId?: string;
  referenceProductId?: string;
  topK?: number;
  includeToolTrace?: boolean;
}): Promise<AssistantResponse> {
  const body = {
    query: params.query,
    conversation_id: params.conversationId,
    user_id: params.userId,
    reference_product_id: params.referenceProductId,
    top_k: params.topK ?? getDefaultTopK(),
    include_tool_trace: params.includeToolTrace ?? true,
  };
  const response = await fetch(withBase("/assistant"), {
    method: "POST",
    headers: defaultHeaders(),
    body: JSON.stringify(body),
  });
  return parseJson<AssistantResponse>(response);
}

export async function fetchConversationHistory(params: {
  conversationId: string;
  userId?: string;
  limit?: number;
}): Promise<AssistantHistoryResponse> {
  const url = new URL(withBase(`/assistant/conversations/${params.conversationId}`), window.location.origin);
  url.searchParams.set("limit", String(params.limit ?? 300));
  if (params.userId) {
    url.searchParams.set("user_id", params.userId);
  }
  const response = await fetch(url.toString(), {
    method: "GET",
    headers: defaultHeaders(),
  });
  return parseJson<AssistantHistoryResponse>(response);
}

export async function fetchSearchStatus(params: {
  conversationId: string;
  userId?: string;
}): Promise<SearchStatusResponse> {
  const url = new URL(withBase(`/assistant/conversations/${params.conversationId}/search-status`), window.location.origin);
  if (params.userId) {
    url.searchParams.set("user_id", params.userId);
  }
  const response = await fetch(url.toString(), {
    method: "GET",
    headers: defaultHeaders(),
  });
  return parseJson<SearchStatusResponse>(response);
}

export async function deleteConversation(params: {
  conversationId: string;
  userId?: string;
}): Promise<DeleteConversationResponse> {
  const url = new URL(withBase(`/assistant/conversations/${params.conversationId}`), window.location.origin);
  if (params.userId) {
    url.searchParams.set("user_id", params.userId);
  }
  const response = await fetch(url.toString(), {
    method: "DELETE",
    headers: defaultHeaders(),
  });
  return parseJson<DeleteConversationResponse>(response);
}

export async function registerMarketplaceAccount(payload: {
  full_name: string;
  email: string;
  password: string;
  role: "buyer" | "seller";
  store_name?: string;
  bio?: string;
}): Promise<MarketplaceAuthResponse> {
  const response = await fetch(withBase("/store/auth/register"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJson<MarketplaceAuthResponse>(response);
}

export async function loginMarketplaceAccount(payload: {
  email: string;
  password: string;
}): Promise<MarketplaceAuthResponse> {
  const response = await fetch(withBase("/store/auth/login"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJson<MarketplaceAuthResponse>(response);
}

export async function fetchMarketplaceMe(token: string): Promise<MarketplaceMeResponse> {
  const response = await fetch(withBase("/store/auth/me"), {
    method: "GET",
    headers: authHeaders(token, false),
  });
  return parseJson<MarketplaceMeResponse>(response);
}

export async function fetchStoreCatalog(params: {
  q?: string;
  category?: string;
  listingType?: "all" | "scraped" | "seller";
  sellerId?: string;
  minPrice?: number;
  maxPrice?: number;
  sort?: "newest" | "relevance" | "price_asc" | "price_desc" | "rating";
  page?: number;
  pageSize?: number;
}): Promise<StoreCatalogResponse> {
  const url = new URL(withBase("/store/catalog"), window.location.origin);
  if (params.q) {
    url.searchParams.set("q", params.q);
  }
  if (params.category) {
    url.searchParams.set("category", params.category);
  }
  if (params.listingType) {
    url.searchParams.set("listing_type", params.listingType);
  }
  if (params.sellerId) {
    url.searchParams.set("seller_id", params.sellerId);
  }
  if (typeof params.minPrice === "number") {
    url.searchParams.set("min_price", String(params.minPrice));
  }
  if (typeof params.maxPrice === "number") {
    url.searchParams.set("max_price", String(params.maxPrice));
  }
  if (params.sort) {
    url.searchParams.set("sort", params.sort);
  }
  url.searchParams.set("page", String(params.page ?? 1));
  url.searchParams.set("page_size", String(params.pageSize ?? 24));
  const response = await fetch(url.toString(), {
    method: "GET",
    headers: authHeaders(undefined, false),
  });
  return parseJson<StoreCatalogResponse>(response);
}

export async function fetchStoreProduct(productId: string): Promise<StoreProductDetailResponse> {
  const response = await fetch(withBase(`/store/products/${productId}`), {
    method: "GET",
    headers: authHeaders(undefined, false),
  });
  return parseJson<StoreProductDetailResponse>(response);
}

export async function fetchProductReviews(productId: string, token?: string): Promise<ProductReviewsResponse> {
  const response = await fetch(withBase(`/store/products/${productId}/reviews`), {
    method: "GET",
    headers: authHeaders(token, false),
  });
  return parseJson<ProductReviewsResponse>(response);
}

export async function submitProductReview(
  token: string,
  productId: string,
  payload: { rating: number; title?: string; body?: string }
): Promise<{ review: ProductReviewsResponse["items"][number]; summary: ProductReviewsResponse["summary"] }> {
  const response = await fetch(withBase(`/store/products/${productId}/reviews`), {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
  return parseJson<{ review: ProductReviewsResponse["items"][number]; summary: ProductReviewsResponse["summary"] }>(response);
}

export async function deleteMyProductReview(
  token: string,
  productId: string
): Promise<{ deleted: boolean; product_id: string; summary: ProductReviewsResponse["summary"] }> {
  const response = await fetch(withBase(`/store/products/${productId}/reviews/me`), {
    method: "DELETE",
    headers: authHeaders(token, false),
  });
  return parseJson<{ deleted: boolean; product_id: string; summary: ProductReviewsResponse["summary"] }>(response);
}

export async function fetchSellerProfile(sellerId: string, page = 1, pageSize = 12): Promise<SellerProfileResponse> {
  const url = new URL(withBase(`/store/sellers/${sellerId}`), window.location.origin);
  url.searchParams.set("page", String(page));
  url.searchParams.set("page_size", String(pageSize));
  const response = await fetch(url.toString(), {
    method: "GET",
    headers: authHeaders(undefined, false),
  });
  return parseJson<SellerProfileResponse>(response);
}

export async function fetchSellerProducts(token: string): Promise<SellerProductsResponse> {
  const response = await fetch(withBase("/store/seller/products"), {
    method: "GET",
    headers: authHeaders(token, false),
  });
  return parseJson<SellerProductsResponse>(response);
}

export async function createSellerProduct(token: string, payload: Record<string, unknown>): Promise<{ product: StoreProduct }> {
  const response = await fetch(withBase("/store/seller/products"), {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
  return parseJson<{ product: StoreProduct }>(response);
}

export async function updateSellerProduct(
  token: string,
  productId: string,
  payload: Record<string, unknown>
): Promise<{ product: StoreProduct }> {
  const response = await fetch(withBase(`/store/seller/products/${productId}`), {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
  return parseJson<{ product: StoreProduct }>(response);
}

export async function deleteSellerProduct(token: string, productId: string): Promise<{ deleted: boolean; product_id: string }> {
  const response = await fetch(withBase(`/store/seller/products/${productId}`), {
    method: "DELETE",
    headers: authHeaders(token, false),
  });
  return parseJson<{ deleted: boolean; product_id: string }>(response);
}

export async function createOrder(
  token: string,
  payload: { product_id: string; quantity: number; shipping_address?: string; notes?: string }
): Promise<{ order: Record<string, unknown> }> {
  const response = await fetch(withBase("/store/orders"), {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
  return parseJson<{ order: Record<string, unknown> }>(response);
}

export async function fetchMyOrders(token: string): Promise<{ items: Record<string, unknown>[] }> {
  const response = await fetch(withBase("/store/orders/me"), {
    method: "GET",
    headers: authHeaders(token, false),
  });
  return parseJson<{ items: Record<string, unknown>[] }>(response);
}

export async function fetchSellerOrders(token: string): Promise<{ items: Record<string, unknown>[] }> {
  const response = await fetch(withBase("/store/seller/orders"), {
    method: "GET",
    headers: authHeaders(token, false),
  });
  return parseJson<{ items: Record<string, unknown>[] }>(response);
}

export async function updateSellerOrderStatus(
  token: string,
  orderId: string,
  status: "pending" | "paid" | "fulfilled" | "cancelled"
): Promise<{ order: Record<string, unknown> }> {
  const response = await fetch(withBase(`/store/seller/orders/${orderId}`), {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify({ status }),
  });
  return parseJson<{ order: Record<string, unknown> }>(response);
}

export async function fetchSellerReportSummary(token: string): Promise<Record<string, unknown>> {
  const response = await fetch(withBase("/store/seller/reports/summary"), {
    method: "GET",
    headers: authHeaders(token, false),
  });
  return parseJson<Record<string, unknown>>(response);
}

export async function fetchSavedReports(params: {
  userId: string;
  reportType?: string;
}): Promise<SavedReportsResponse> {
  const url = new URL(withBase("/reports"), window.location.origin);
  url.searchParams.set("user_id", params.userId);
  if (params.reportType) {
    url.searchParams.set("report_type", params.reportType);
  }
  const response = await fetch(url.toString(), {
    method: "GET",
    headers: defaultHeaders(),
  });
  return parseJson<SavedReportsResponse>(response);
}

export async function fetchSavedReport(params: {
  reportId: string;
  userId: string;
}): Promise<SavedReportDetailResponse> {
  const url = new URL(withBase(`/reports/${params.reportId}`), window.location.origin);
  url.searchParams.set("user_id", params.userId);
  const response = await fetch(url.toString(), {
    method: "GET",
    headers: defaultHeaders(),
  });
  return parseJson<SavedReportDetailResponse>(response);
}

export async function deleteSavedReport(params: {
  reportId: string;
  userId: string;
}): Promise<{ deleted: boolean; report_id: string }> {
  const url = new URL(withBase(`/reports/${params.reportId}`), window.location.origin);
  url.searchParams.set("user_id", params.userId);
  const response = await fetch(url.toString(), {
    method: "DELETE",
    headers: defaultHeaders(),
  });
  return parseJson<{ deleted: boolean; report_id: string }>(response);
}

export function buildSavedReportPdfUrl(params: { reportId: string; userId: string }): string {
  const url = new URL(withBase(`/reports/${params.reportId}/pdf`), window.location.origin);
  url.searchParams.set("user_id", params.userId);
  return url.toString();
}

export async function downloadSavedReportPdf(params: { reportId: string; userId: string }): Promise<void> {
  const response = await fetch(buildSavedReportPdfUrl(params), {
    method: "GET",
    headers: defaultHeaders(),
  });
  if (!response.ok) {
    await parseJson(response);
    return;
  }
  const blob = await response.blob();
  const objectUrl = window.URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = `${params.reportId}.pdf`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    window.URL.revokeObjectURL(objectUrl);
  }
}
