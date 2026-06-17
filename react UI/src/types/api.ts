export type AssistantOffer = {
  offer_id?: string | null;
  title?: string;
  link?: string;
  source?: string;
  image?: string | null;
  price_pkr?: number | null;
  total_price_pkr?: number | null;
  rating?: number | null;
  review_count?: number | null;
  reason?: string | null;
  match_score?: number | null;
  listing_type?: "scraped" | "seller" | null;
  internal_path?: string | null;
  external_url?: string | null;
  seller_id?: string | null;
  seller_name?: string | null;
  store_name?: string | null;
  source_rating?: number | null;
  source_review_count?: number | null;
  display_source_rating?: number | null;
  display_source_review_count?: number | null;
  display_source_rating_kind?: "scraped" | "predicted" | "missing" | null;
  price_range_pkr_min?: number | null;
  price_range_pkr_max?: number | null;
  app_rating?: number | null;
  app_review_count?: number | null;
  units_sold?: number | null;
  order_count?: number | null;
  revenue_pkr?: number | null;
  predicted_app_rating?: number | null;
  predicted_demand_score?: number | null;
  seasonal_relevance_score?: number | null;
  best_months?: number[] | null;
  best_month_labels?: string[] | null;
};

export type AssistantContext = {
  intent?: string;
  mode_label?: string;
  response_focus?: string;
  summary?: string;
  decision_reason?: string;
  selected_offer?: AssistantOffer | null;
  comparison_offers?: AssistantOffer[];
  results_query?: string;
  report_id?: string;
};

export type AssistantToolCall = {
  tool_name?: string;
  arguments?: Record<string, unknown>;
  output?: Record<string, unknown>;
};

export type AssistantResponse = {
  conversation_id: string;
  mode: string;
  answer: string;
  results: AssistantOffer[];
  search_phase?: "local_partial" | "complete";
  search_status?: "local_ready" | "online_searching" | "complete";
  pending_online_refresh?: boolean;
  tool_calls?: AssistantToolCall[];
  fallback_reason?: string;
  intent?: string;
  plan_summary?: string;
  assistant_context?: AssistantContext;
};

export type SearchStatusResponse = {
  conversation_id: string;
  query: string;
  search_phase: "local_partial" | "complete";
  search_status: "local_ready" | "online_searching" | "complete";
  pending_online_refresh: boolean;
  local_results: AssistantOffer[];
  online_results: AssistantOffer[];
  merged_results: AssistantOffer[];
  notice?: string | null;
  report_id?: string | null;
};

export type AssistantHistoryTurn = {
  seq: number;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_name?: string;
  metadata?: Record<string, unknown>;
  ts?: string;
};

export type AssistantHistoryResponse = {
  conversation_id: string;
  count: number;
  turns: AssistantHistoryTurn[];
};

export type DeleteConversationResponse = {
  conversation_id: string;
  turns_deleted: number;
  session_deleted: number;
  tool_logs_deleted?: number;
};

export type MarketplaceRole = "buyer" | "seller";

export type MarketplaceUser = {
  user_id: string;
  full_name: string;
  email: string;
  role: MarketplaceRole;
  store_name?: string | null;
  bio?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type StoreProduct = {
  product_id: string;
  offer_id?: string | null;
  listing_type: "scraped" | "seller";
  title: string;
  description?: string | null;
  category?: string | null;
  subcategory?: string | null;
  brand?: string | null;
  model?: string | null;
  price_pkr?: number | null;
  shipping_pkr?: number | null;
  total_price_pkr?: number | null;
  in_stock: boolean;
  stock_qty?: number | null;
  image?: string | null;
  images?: string[];
  specifications?: string | null;
  tags?: string[];
  external_url?: string | null;
  internal_path?: string | null;
  seller_id?: string | null;
  seller_name?: string | null;
  store_name?: string | null;
  source?: string | null;
  source_label?: string | null;
  status?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  published_at?: string | null;
  rating?: number | null;
  review_count?: number | null;
  source_rating?: number | null;
  source_review_count?: number | null;
  display_source_rating?: number | null;
  display_source_review_count?: number | null;
  display_source_rating_kind?: "scraped" | "predicted" | "missing" | null;
  price_range_pkr_min?: number | null;
  price_range_pkr_max?: number | null;
  app_rating?: number | null;
  app_review_count?: number | null;
  app_rating_breakdown?: Record<number, number>;
  units_sold?: number | null;
  order_count?: number | null;
  revenue_pkr?: number | null;
  predicted_app_rating?: number | null;
  predicted_demand_score?: number | null;
  seasonal_relevance_score?: number | null;
  best_months?: number[];
  best_month_labels?: string[];
  interaction_funnel?: Record<string, number>;
  link?: string | null;
};

export type ProductReviewSummary = {
  average_rating?: number | null;
  review_count: number;
  breakdown: Record<number, number>;
};

export type ProductReview = {
  review_id: string;
  product_id: string;
  offer_id?: string | null;
  listing_type?: "scraped" | "seller" | null;
  user_id: string;
  user_name?: string | null;
  user_role?: string | null;
  rating: number;
  title?: string | null;
  body?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type StoreCatalogResponse = {
  query: string;
  category?: string | null;
  listing_type: "all" | "scraped" | "seller";
  sort: "newest" | "relevance" | "price_asc" | "price_desc" | "rating";
  page: number;
  page_size: number;
  total: number;
  pages: number;
  categories: string[];
  items: StoreProduct[];
};

export type StoreProductDetailResponse = {
  product: StoreProduct;
  seller?: MarketplaceUser | null;
};

export type ProductReviewsResponse = {
  product_id: string;
  summary: ProductReviewSummary;
  total: number;
  page: number;
  page_size: number;
  items: ProductReview[];
  my_review?: ProductReview | null;
};

export type MarketplaceAuthResponse = {
  token: string;
  user: MarketplaceUser;
};

export type MarketplaceMeResponse = {
  user: MarketplaceUser;
};

export type SellerProductsResponse = {
  items: StoreProduct[];
};

export type SellerProfileResponse = {
  seller: MarketplaceUser;
  products: StoreCatalogResponse;
};

export type MarketplaceOrder = {
  order_id: string;
  product_id: string;
  offer_id?: string | null;
  listing_type?: "scraped" | "seller" | null;
  buyer_id?: string | null;
  buyer_name?: string | null;
  seller_id?: string | null;
  seller_name?: string | null;
  store_name?: string | null;
  title?: string | null;
  category?: string | null;
  subcategory?: string | null;
  price_pkr?: number | null;
  shipping_pkr?: number | null;
  quantity: number;
  subtotal_pkr?: number | null;
  total_pkr?: number | null;
  status: "pending" | "paid" | "fulfilled" | "cancelled";
  created_at?: string | null;
  paid_at?: string | null;
  fulfilled_at?: string | null;
  updated_at?: string | null;
};

export type SavedReport = {
  report_id: string;
  owner_user_id: string;
  report_type: string;
  title: string;
  conversation_id?: string | null;
  seller_id?: string | null;
  source_kind?: string | null;
  payload?: Record<string, unknown>;
  pdf_path?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type SavedReportsResponse = {
  items: SavedReport[];
  count: number;
};

export type SavedReportDetailResponse = {
  report: SavedReport;
};
