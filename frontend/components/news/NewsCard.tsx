"use client";

import React from "react";
import type { NewsArticle } from "./types";
import { formatFullDateTime, formatRelativeTime, getSentimentStyle, sourceInitial } from "@/lib/newsFormat";

interface NewsCardProps {
  article: NewsArticle;
  onSelect: (article: NewsArticle) => void;
}

/**
 * One story. The preview is a real <button> and the external link sits beside it, not
 * inside it: interactive controls must not nest. Enter on the button opens the preview
 * natively, and the calculator's key handler ignores keys inside the news feed
 * ([data-news-feed]), so Enter here no longer also presses "=".
 */
export function NewsCard({ article, onSelect }: NewsCardProps) {
  const sentiment = getSentimentStyle(article.sentiment);
  const relativeTime = formatRelativeTime(article.published_at, Date.now(), article.date_only);
  const moreSources = article.related?.length ?? 0;

  return (
    <div
      className="group relative flex items-start gap-1 rounded-lg border transition-all duration-150 hover:border-[var(--mf-accent)] hover:shadow-sm focus-within:border-[var(--mf-accent)]"
      style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}
    >
      <button
        type="button"
        onClick={() => onSelect(article)}
        className="min-w-0 flex-1 rounded-lg p-2.5 pr-0 text-left focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--mf-accent)]"
        aria-label={`Preview: ${article.title}`}
      >
        <span className="flex items-center justify-between gap-2">
          <span className="flex min-w-0 items-center gap-1.5">
            <span
              className="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-sm text-[0.55rem] font-bold"
              style={{ background: "rgba(100, 116, 139, 0.15)", color: "var(--mf-muted)" }}
              aria-hidden="true"
            >
              {sourceInitial(article.source)}
            </span>
            <span className="max-w-[110px] truncate text-[0.68rem] font-semibold sm:max-w-[140px]" style={{ color: "var(--mf-muted)" }} title={article.source}>
              {article.source}
            </span>
            {moreSources > 0 && (
              <span className="shrink-0 text-[0.62rem] font-medium" style={{ color: "var(--mf-muted)" }}>
                +{moreSources} {moreSources === 1 ? "source" : "sources"}
              </span>
            )}
            <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: sentiment.dotColor }} title={`Sentiment: ${sentiment.label}`} aria-hidden="true" />
            <span className="sr-only">Sentiment: {sentiment.label}.</span>
          </span>
          <time
            className="shrink-0 text-[0.68rem] tabular-nums"
            style={{ color: "var(--mf-muted)" }}
            dateTime={article.published_at ?? undefined}
            title={formatFullDateTime(article.published_at, article.date_only)}
          >
            {relativeTime}
          </time>
        </span>
        <span className="mt-1 line-clamp-2 block text-xs font-medium leading-snug transition-colors group-hover:text-[var(--mf-accent)]" style={{ color: "var(--mf-fg)" }}>
          {article.title}
        </span>
      </button>

      <a
        href={article.url}
        target="_blank"
        rel="noopener noreferrer"
        title="Open original article in new tab"
        aria-label={`Open original article: ${article.title}`}
        className="m-1.5 shrink-0 rounded p-1 transition-colors hover:text-[var(--mf-accent)]"
        style={{ color: "var(--mf-muted)" }}
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
          <polyline points="15 3 21 3 21 9" />
          <line x1="10" y1="14" x2="21" y2="3" />
        </svg>
      </a>
    </div>
  );
}
