"use client";

import React, { useRef, useState } from "react";
import { NEWS_CATEGORIES, type NewsCategoryFilter } from "./types";

/** "live": a sweep succeeded within the hour. "delayed": none has. "offline": the
 * database could not be read. "checking": the backend has not swept since it started. */
export type FeedLiveness = "live" | "delayed" | "offline" | "checking";

interface NewsFilterBarProps {
  activeCategory: NewsCategoryFilter;
  onSelectCategory: (category: NewsCategoryFilter) => void;
  searchQuery: string;
  onSearchChange: (query: string) => void;
  onRefresh: () => void;
  isRefreshing?: boolean;
  liveness: FeedLiveness;
}

const LIVENESS: Record<FeedLiveness, { label: string; color: string; bg: string; pulse: boolean }> = {
  live: { label: "Live", color: "var(--mf-accent)", bg: "var(--mf-accent-bg)", pulse: true },
  delayed: { label: "Delayed", color: "var(--mf-warning)", bg: "var(--mf-warning-bg)", pulse: false },
  offline: { label: "Offline", color: "var(--mf-danger)", bg: "var(--mf-danger-bg)", pulse: false },
  checking: { label: "Checking feeds", color: "var(--mf-muted)", bg: "rgba(100, 116, 139, 0.12)", pulse: false },
};

export function NewsFilterBar({
  activeCategory,
  onSelectCategory,
  searchQuery,
  onSearchChange,
  onRefresh,
  isRefreshing = false,
  liveness,
}: NewsFilterBarProps) {
  const [searchOpen, setSearchOpen] = useState(Boolean(searchQuery));
  const searchInputRef = useRef<HTMLInputElement>(null);
  const badge = LIVENESS[liveness];

  const toggleSearch = () => {
    if (searchOpen && !searchQuery) {
      setSearchOpen(false);
    } else {
      setSearchOpen(true);
      setTimeout(() => searchInputRef.current?.focus(), 50);
    }
  };

  return (
    <div className="flex flex-col gap-2">
      {/* Top action row */}
      <div className="flex items-center justify-between gap-2">
        <span
          className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[0.68rem] font-semibold tracking-wide uppercase"
          style={{ background: badge.bg, color: badge.color }}
        >
          <span className="relative flex h-1.5 w-1.5" aria-hidden="true">
            {badge.pulse && (
              <span className="absolute inline-flex h-full w-full rounded-full opacity-75 motion-safe:animate-ping" style={{ background: badge.color }} />
            )}
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full" style={{ background: badge.color }} />
          </span>
          {badge.label}
        </span>

        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={toggleSearch}
            title={searchOpen ? "Close search" : "Search headlines and sources"}
            aria-label={searchOpen ? "Close search" : "Search news"}
            aria-expanded={searchOpen}
            className="flex h-7 w-7 items-center justify-center rounded-md border transition-colors hover:text-[var(--mf-fg)]"
            style={{
              borderColor: searchOpen ? "var(--mf-accent)" : "var(--mf-border)",
              background: searchOpen ? "var(--mf-accent-bg)" : "transparent",
              color: searchOpen ? "var(--mf-accent)" : "var(--mf-muted)",
            }}
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
          </button>

          <button
            type="button"
            onClick={onRefresh}
            disabled={isRefreshing}
            title="Fetch the feeds now (at most once a minute)"
            aria-label="Refresh news feed"
            className="flex h-7 w-7 items-center justify-center rounded-md border transition-colors hover:text-[var(--mf-fg)] disabled:opacity-50"
            style={{ borderColor: "var(--mf-border)", color: "var(--mf-muted)", background: "transparent" }}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
              className={isRefreshing ? "motion-safe:animate-spin text-[var(--mf-accent)]" : ""}
            >
              <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
            </svg>
          </button>
        </div>
      </div>

      {searchOpen && (
        <div className="relative">
          <input
            ref={searchInputRef}
            type="search"
            aria-label="Search news headlines, summaries and sources"
            placeholder="Search headlines or sources..."
            value={searchQuery}
            maxLength={100}
            onChange={(e) => onSearchChange(e.target.value)}
            className="w-full rounded-md border px-2.5 py-1 pr-7 text-xs outline-none transition-colors focus:border-[var(--mf-accent)]"
            style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-fg)" }}
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => onSearchChange("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-xs font-bold hover:opacity-80"
              style={{ color: "var(--mf-muted)" }}
              aria-label="Clear search"
            >
              ✕
            </button>
          )}
        </div>
      )}

      {/* Category chips: horizontally scrollable on narrow screens */}
      <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar whitespace-nowrap py-1" role="group" aria-label="Filter by category">
        {NEWS_CATEGORIES.map(({ slug, label }) => {
          const isActive = activeCategory === slug;
          return (
            <button
              key={slug}
              type="button"
              aria-pressed={isActive}
              onClick={() => onSelectCategory(slug)}
              className="rounded-full border px-2.5 py-0.5 text-[0.72rem] font-medium transition-all shrink-0"
              style={{
                borderColor: isActive ? "var(--mf-accent)" : "var(--mf-border)",
                background: isActive ? "var(--mf-accent-bg)" : "transparent",
                color: isActive ? "var(--mf-accent)" : "var(--mf-muted)",
              }}
            >
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
