import { keepPreviousData, useQuery, UseQueryResult } from "@tanstack/react-query";
import { ApiError, apiGet } from "./client";
import type { NewsFilters, NewsResponse, NewsSourcesResponse, RefreshResponse } from "@/components/news/types";

/** Query parameters for GET /api/news: slugs only, and nothing for "all". */
export function buildNewsParams(filters: NewsFilters = {}): Record<string, string | number | undefined> {
  const params: Record<string, string | number | undefined> = {};
  if (filters.category && filters.category !== "all") params.category = filters.category;
  if (filters.region) params.region = filters.region;
  const search = filters.search?.trim();
  if (search) params.search = search.slice(0, 100);
  if (filters.limit) params.limit = filters.limit;
  return params;
}

export async function getGlobalNews(filters: NewsFilters = {}): Promise<NewsResponse> {
  return apiGet<NewsResponse>("/api/news", buildNewsParams(filters));
}

export async function getNewsSources(): Promise<NewsSourcesResponse> {
  return apiGet<NewsSourcesResponse>("/api/news/sources");
}

/** The header the backend requires on refresh: a custom header makes a cross-site request
 * preflight, which CORS refuses, so another page cannot trigger sweeps. */
export const NEWS_REFRESH_HEADERS = { "X-Requested-With": "myfinonce" } as const;

export async function refreshGlobalNews(): Promise<RefreshResponse> {
  const res = await fetch("/api/news/refresh", { method: "POST", headers: { ...NEWS_REFRESH_HEADERS } });
  if (!res.ok) throw new ApiError(res.status, (await res.text().catch(() => "")) || res.statusText);
  return res.json();
}

// The backend sweeps every 20 minutes and caches for 15, so polling faster than every
// 10 minutes only re-reads the same rows.
const NEWS_STALE_MS = 5 * 60_000;
const NEWS_POLL_MS = 10 * 60_000;

export function useGlobalNews(filters: NewsFilters = {}): UseQueryResult<NewsResponse, Error> {
  return useQuery<NewsResponse, Error>({
    queryKey: ["global-news", buildNewsParams(filters)],
    queryFn: () => getGlobalNews(filters),
    staleTime: NEWS_STALE_MS,
    refetchInterval: NEWS_POLL_MS,
    refetchOnWindowFocus: true,
    // Keep the current list on screen while a new filter or search loads, instead of
    // flashing the skeleton on every keystroke.
    placeholderData: keepPreviousData,
  });
}

export function useNewsSources(enabled: boolean): UseQueryResult<NewsSourcesResponse, Error> {
  return useQuery<NewsSourcesResponse, Error>({
    queryKey: ["global-news-sources"],
    queryFn: getNewsSources,
    enabled,
    staleTime: NEWS_STALE_MS,
    refetchInterval: enabled ? NEWS_POLL_MS : false,
  });
}
