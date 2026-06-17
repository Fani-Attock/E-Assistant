import type { AssistantContext } from "./api";

export type Role = "user" | "assistant";

export type ToolTrace = {
  tool: string;
  type: "database" | "web" | "ranking";
  description: string;
  status: "success" | "error";
  duration?: number;
};

export type Product = {
  id: string;
  name: string;
  image: string | null;
  fallbackImage: string | null;
  price: number | null;
  originalPrice?: number;
  rating: number | null;
  reviewCount: number | null;
  sourceRating?: number | null;
  sourceReviewCount?: number | null;
  displaySourceRating?: number | null;
  displaySourceReviewCount?: number | null;
  displaySourceRatingKind?: "scraped" | "predicted" | "missing" | null;
  priceRangeMin?: number | null;
  priceRangeMax?: number | null;
  appRating?: number | null;
  appReviewCount?: number | null;
  unitsSold?: number | null;
  orderCount?: number | null;
  revenuePkr?: number | null;
  predictedAppRating?: number | null;
  predictedDemandScore?: number | null;
  seasonalRelevanceScore?: number | null;
  bestMonthLabels?: string[] | null;
  source: string;
  url: string;
  listingType?: "scraped" | "seller";
  internalPath?: string | null;
  externalUrl?: string | null;
  sellerId?: string | null;
  sellerName?: string | null;
  storeName?: string | null;
  reason?: string;
};

export type Message = {
  id: string;
  role: Role;
  content: string;
  timestamp: number;
  products?: Product[];
  traces?: ToolTrace[];
  assistantContext?: AssistantContext;
};

export type ConversationSummary = {
  conversationId: string;
  query: string;
  answer: string;
  resultCount: number;
  timestamp: number;
};

export type SearchLifecycle = {
  phase: "idle" | "local_partial" | "complete";
  status: "idle" | "local_ready" | "online_searching" | "complete";
  pendingOnlineRefresh: boolean;
  notice?: string;
};
