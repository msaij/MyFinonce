"use client";

import React, { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { NewsArticle, NewsCategoryFilter, NewsResponse } from "./types";
import { refreshGlobalNews, useGlobalNews, useNewsSources } from "@/lib/api/news";
import { formatIstTime } from "@/lib/newsFormat";
import { NewsFilterBar, type FeedLiveness } from "./NewsFilterBar";
import { NewsCard } from "./NewsCard";
import { NewsSkeleton } from "./NewsSkeleton";
import { NewsPreviewDrawer } from "./NewsPreviewDrawer";
import { NewsSources } from "./NewsSources";

function livenessOf(data: NewsResponse | undefined): FeedLiveness {
  if (!data) return "checking";
  if (data.is_offline) return "offline";
  if (!data.last_sweep_ok_at && data.feeds_pending === data.feeds_total) return "checking";
  return data.is_stale ? "delayed" : "live";
}

const bannerStyle = { background: "var(--mf-warning-bg)", borderColor: "var(--mf-warning)", color: "var(--mf-warning)" };

export function GlobalNewsFeed() {
  const [activeCategory, setActiveCategory] = useState<NewsCategoryFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [selectedArticle, setSelectedArticle] = useState<NewsArticle | null>(null);
  const [isManualRefreshing, setIsManualRefreshing] = useState(false);
  const [refreshNote, setRefreshNote] = useState<string | null>(null);
  const [showSources, setShowSources] = useState(false);
  const queryClient = useQueryClient();

  // Debounce search so each keystroke is not a request.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(searchQuery.trim()), 250);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // The server filters by slug and search; there is no second, client-side filter.
  const { data, isLoading, isError, error, refetch, isPlaceholderData } = useGlobalNews({
    category: activeCategory,
    search: debouncedSearch || undefined,
  });
  const sources = useNewsSources(showSources);

  const handleRefresh = async () => {
    setIsManualRefreshing(true);
    setRefreshNote(null);
    try {
      const res = await refreshGlobalNews();
      if (res.status !== "ok") setRefreshNote(res.message);
    } catch {
      setRefreshNote("Could not reach the backend to refresh the feeds.");
    } finally {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["global-news"] }),
        queryClient.invalidateQueries({ queryKey: ["global-news-sources"] }),
      ]);
      setIsManualRefreshing(false);
    }
  };

  const articles = data?.articles ?? [];
  const liveness = livenessOf(data);
  const hasFilters = activeCategory !== "all" || Boolean(debouncedSearch);

  return (
    // data-news-feed: the calculator's window key handler ignores keys from inside here
    // (and while the preview dialog is open), so Enter and Escape mean what they say.
    <div data-news-feed className="flex flex-col rounded-xl border p-4" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
      <div className="mb-3">
        <h3 className="text-sm font-bold" style={{ color: "var(--mf-fg)" }}>
          Global Financial News
        </h3>
        <p className="mt-0.5 text-xs" style={{ color: "var(--mf-muted)" }}>
          Central banks and regulators, world markets, FX and commodities, and India, from {data?.feeds_total ?? "the"} sources.
          Kept for {data?.retention_days ?? 30} days.
        </p>
        {data && (
          <button
            type="button"
            onClick={() => setShowSources((s) => !s)}
            aria-expanded={showSources}
            className="mt-1 text-[0.68rem] hover:underline"
            style={{ color: "var(--mf-muted)" }}
          >
            {data.last_sweep_ok_at ? `Updated ${formatIstTime(data.last_sweep_ok_at)}` : "Not updated yet"}
            {" · "}
            {data.feeds_pending === data.feeds_total
              ? `${data.feeds_total} sources`
              : `${data.feeds_ok}/${data.feeds_total} sources OK`}
            {showSources ? " ▲" : " ▼"}
          </button>
        )}
      </div>

      {showSources && (
        <div className="mb-3">
          <NewsSources data={sources.data} isLoading={sources.isLoading} error={sources.error} />
        </div>
      )}

      <NewsFilterBar
        activeCategory={activeCategory}
        onSelectCategory={setActiveCategory}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        onRefresh={handleRefresh}
        isRefreshing={isManualRefreshing}
        liveness={liveness}
      />

      {refreshNote && (
        <p className="mt-2 text-[0.7rem]" style={{ color: "var(--mf-muted)" }} role="status">
          {refreshNote}
        </p>
      )}

      {/* Only claim "cached" when cached articles are actually on screen. */}
      {data?.is_offline && articles.length > 0 && (
        <div className="mt-3 rounded-lg border px-3 py-2 text-xs" style={bannerStyle} role="status">
          Couldn&apos;t reach the news database. Showing the articles loaded at {formatIstTime(data.cached_at) || "an earlier time"}.
        </div>
      )}
      {data && !data.is_offline && data.is_stale && liveness === "delayed" && (
        <div className="mt-3 rounded-lg border px-3 py-2 text-xs" style={bannerStyle} role="status">
          {data.last_sweep_ok_at
            ? `The feeds haven't updated since ${formatIstTime(data.last_sweep_ok_at)}`
            : "No feed has been fetched successfully yet"}
          {data.feeds_failing ? ` (${data.feeds_failing} of ${data.feeds_total} sources failing)` : ""}. Showing stored articles.
        </div>
      )}

      <div className="mt-3 flex-1">
        {isLoading && !data ? (
          <NewsSkeleton count={5} />
        ) : isError && !data ? (
          <div className="flex flex-col items-center justify-center rounded-lg border border-dashed px-4 py-8 text-center" style={{ borderColor: "var(--mf-border)", color: "var(--mf-muted)" }}>
            <p className="text-xs font-medium">Unable to load the news feed.</p>
            <p className="mt-1 text-[0.7rem]">{error instanceof Error ? error.message : "Network error or service unavailable."}</p>
            <button
              type="button"
              onClick={() => refetch()}
              className="mt-3 rounded-md px-3 py-1 text-xs font-semibold transition-opacity hover:opacity-90"
              style={{ background: "var(--mf-accent)", color: "#ffffff" }}
            >
              Retry
            </button>
          </div>
        ) : articles.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-lg border border-dashed px-4 py-8 text-center" style={{ borderColor: "var(--mf-border)", color: "var(--mf-muted)" }}>
            <p className="text-xs font-medium">
              {data?.is_offline
                ? "The news database is unreachable right now, and nothing was loaded earlier to fall back on."
                : hasFilters
                  ? "No articles match these filters."
                  : "No articles yet. The feeds are fetched every 20 minutes."}
            </p>
            {data?.is_offline ? (
              <button type="button" onClick={() => refetch()} className="mt-2 text-xs font-semibold hover:underline" style={{ color: "var(--mf-accent)" }}>
                Retry
              </button>
            ) : (
              hasFilters && (
                <button
                  type="button"
                  onClick={() => {
                    setActiveCategory("all");
                    setSearchQuery("");
                  }}
                  className="mt-2 text-xs font-semibold hover:underline"
                  style={{ color: "var(--mf-accent)" }}
                >
                  Clear filters
                </button>
              )
            )}
          </div>
        ) : (
          <div
            className="max-h-[30rem] space-y-2 overflow-y-auto pr-1 transition-opacity"
            style={{ opacity: isPlaceholderData ? 0.6 : 1 }}
            aria-label="Financial news articles"
            aria-busy={isPlaceholderData}
          >
            {articles.map((article) => (
              <NewsCard key={article.id} article={article} onSelect={setSelectedArticle} />
            ))}
          </div>
        )}
      </div>

      <NewsPreviewDrawer article={selectedArticle} onClose={() => setSelectedArticle(null)} />
    </div>
  );
}
