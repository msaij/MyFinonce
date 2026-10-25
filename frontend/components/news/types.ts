/**
 * The news feed's API contract. The category and region slugs mirror
 * backend/app/services/news_service.py (CATEGORY_LABELS, REGION_LABELS) and the router's
 * Literal types; lib/news.test.ts and backend/tests/test_news.py pin the same literal
 * lists, so change both sides together. The API rejects anything else with a 422.
 */

export type NewsCategorySlug = "central_banks" | "macro" | "equities" | "commodities_forex";
/** "all" is the UI's own value for "no filter": it is never sent. */
export type NewsCategoryFilter = NewsCategorySlug | "all";

export const NEWS_CATEGORIES: ReadonlyArray<{ slug: NewsCategoryFilter; label: string }> = [
  { slug: "all", label: "All" },
  { slug: "central_banks", label: "Central Banks" },
  { slug: "macro", label: "Macro" },
  { slug: "equities", label: "Equities" },
  { slug: "commodities_forex", label: "FX & Commodities" },
];

export type NewsRegionSlug = "global" | "india" | "us" | "uk" | "europe" | "asia";

export const NEWS_REGION_LABELS: Record<NewsRegionSlug, string> = {
  global: "Global",
  india: "India",
  us: "US",
  uk: "UK",
  europe: "Europe",
  asia: "Asia-Pacific",
};

export type NewsSentiment = "positive" | "negative" | "neutral";

export interface RelatedArticle {
  id: string;
  source: string;
  url: string;
  title: string;
  published_at: string | null;
}

export interface NewsArticle {
  id: string;
  title: string;
  summary: string;
  source: string;
  url: string;
  category: NewsCategorySlug | string;
  region: NewsRegionSlug | string;
  published_at: string | null;
  /** The feed gives a date with no time of day (SEBI, BIS): show the day, not "3h ago". */
  date_only: boolean;
  sentiment: NewsSentiment | string;
  keywords: string[];
  fetched_at: string | null;
  paywall: "partial" | "soft" | null;
  official: boolean;
  /** The same story from other outlets, merged into this card. */
  related: RelatedArticle[];
}

export interface NewsFreshness {
  last_sweep_at: string | null;
  last_sweep_ok_at: string | null;
  feeds_ok: number;
  feeds_total: number;
  feeds_stale: number;
  feeds_failing: number;
  feeds_pending: number;
}

export interface NewsResponse extends NewsFreshness {
  status: string;
  /** Stories matching the filters. */
  total: number;
  /** Stories in this page. */
  count: number;
  /** The database was unreachable: articles (if any) are the last ones loaded, at cached_at. */
  is_offline: boolean;
  /** No sweep has succeeded within the last hour. */
  is_stale: boolean;
  articles: NewsArticle[];
  last_updated: string | null;
  cached_at: string | null;
  retention_days: number;
}

export type FeedState = "pending" | "ok" | "stale" | "failing" | "backoff";

export interface FeedHealth {
  name: string;
  source: string;
  url: string;
  category: string;
  region: string;
  official: boolean;
  paywall: "partial" | "soft" | null;
  state: FeedState;
  last_attempt_at: string | null;
  last_ok_at: string | null;
  http_status: number | null;
  items_parsed: number;
  items_kept: number;
  items_new: number;
  newest_item_at: string | null;
  consecutive_failures: number;
  last_error: string | null;
  backoff_until: string | null;
  dropped: Record<string, number>;
  stored: number;
  stored_newest_at: string | null;
  stale_after_days: number;
}

export interface NewsSourcesResponse extends NewsFreshness {
  feeds: FeedHealth[];
  is_stale: boolean;
  next_sweep_at: string | null;
  daemon_running: boolean;
  retention_days: number;
}

export interface RefreshResponse {
  status: "ok" | "failed" | "recent" | "busy";
  message: string;
  details: Record<string, unknown>;
}

export interface NewsFilters {
  category?: NewsCategoryFilter;
  region?: NewsRegionSlug;
  search?: string;
  limit?: number;
}
