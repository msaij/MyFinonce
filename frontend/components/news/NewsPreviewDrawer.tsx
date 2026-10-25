"use client";

import React, { useEffect, useRef } from "react";
import type { NewsArticle } from "./types";
import {
  categoryLabel,
  formatFullDateTime,
  formatRelativeTime,
  getDomain,
  getSentimentStyle,
  paywallNote,
  regionLabel,
  sourceInitial,
} from "@/lib/newsFormat";

interface NewsPreviewDrawerProps {
  article: NewsArticle | null;
  onClose: () => void;
}

const FOCUSABLE = 'a[href], button:not([disabled]), input, [tabindex]:not([tabindex="-1"])';

export function NewsPreviewDrawer({ article, onClose }: NewsPreviewDrawerProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!article) return;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // Captured on window and stopped here: the calculator behind this dialog also
        // listens on window, and Escape there clears its expression.
        e.preventDefault();
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      const panel = panelRef.current;
      if (e.key !== "Tab" || !panel) return;
      // Keep Tab inside the dialog: aria-modal promises the page behind is inert.
      const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      const outside = !panel.contains(active);
      if (e.shiftKey && (active === first || outside)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || outside)) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown, true);

    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      document.body.style.overflow = originalOverflow;
      previouslyFocused?.focus();
    };
  }, [article]);

  if (!article) return null;

  const domain = getDomain(article.url);
  const sentiment = getSentimentStyle(article.sentiment);
  const relativeTime = formatRelativeTime(article.published_at, Date.now(), article.date_only);
  const fullTime = formatFullDateTime(article.published_at, article.date_only);
  const region = regionLabel(article.region);
  const paywall = paywallNote(article.paywall);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-labelledby="news-drawer-title">
      <div className="fixed inset-0 bg-black/40 backdrop-blur-sm transition-opacity duration-200" onClick={onClose} aria-hidden="true" />

      <div
        ref={panelRef}
        className="relative z-10 flex h-full w-full max-w-lg flex-col justify-between overflow-y-auto border-l p-6 shadow-2xl"
        style={{ background: "var(--mf-bg)", borderColor: "var(--mf-border)", color: "var(--mf-fg)" }}
      >
        <div>
          <div className="flex items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <span
                className="rounded-full px-2.5 py-0.5 text-[0.7rem] font-semibold tracking-wide uppercase"
                style={{ background: "var(--mf-accent-bg)", color: "var(--mf-accent)" }}
              >
                {categoryLabel(article.category)}
              </span>
              {region && (
                <span className="rounded-full border px-2 py-0.5 text-[0.68rem] font-medium" style={{ borderColor: "var(--mf-border)", color: "var(--mf-muted)" }}>
                  {region}
                </span>
              )}
              <span
                className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[0.68rem] font-semibold"
                style={{ background: sentiment.badgeBg, color: sentiment.textColor }}
                title={article.official ? "Primary sources are not scored" : "Estimated from the headline's wording"}
              >
                <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: sentiment.dotColor }} aria-hidden="true" />
                {sentiment.label}
              </span>
            </div>

            <button
              ref={closeRef}
              type="button"
              onClick={onClose}
              className="rounded-md p-1.5 transition-colors hover:bg-slate-100"
              style={{ color: "var(--mf-muted)" }}
              aria-label="Close article preview"
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>

          <h3 id="news-drawer-title" className="mt-4 text-lg font-bold leading-snug sm:text-xl" style={{ color: "var(--mf-fg)" }}>
            {article.title}
          </h3>

          <div className="mt-3 flex flex-wrap items-center gap-2 border-b pb-3 text-xs" style={{ borderColor: "var(--mf-border)", color: "var(--mf-muted)" }}>
            <div className="flex items-center gap-1.5">
              <span
                className="flex h-4 w-4 items-center justify-center rounded-sm text-[0.6rem] font-bold"
                style={{ background: "rgba(100, 116, 139, 0.15)", color: "var(--mf-muted)" }}
                aria-hidden="true"
              >
                {sourceInitial(article.source)}
              </span>
              <span className="font-semibold" style={{ color: "var(--mf-fg)" }}>
                {article.source}
              </span>
              {domain && <span>({domain})</span>}
            </div>
            <span aria-hidden="true">•</span>
            <time dateTime={article.published_at ?? undefined}>{fullTime || relativeTime}</time>
            {relativeTime && fullTime && <span className="text-[0.7rem]">({relativeTime})</span>}
          </div>

          <div className="mt-5 text-sm leading-relaxed" style={{ color: "var(--mf-fg)" }}>
            {article.summary ? (
              <p className="whitespace-pre-line">{article.summary}</p>
            ) : (
              <p className="text-xs italic" style={{ color: "var(--mf-muted)" }}>
                The feed gives no summary for this item. Open it on {domain || "the publisher's site"} to read it.
              </p>
            )}
          </div>

          {article.related && article.related.length > 0 && (
            <div className="mt-6">
              <h4 className="text-[0.7rem] font-semibold uppercase tracking-wide" style={{ color: "var(--mf-muted)" }}>
                Also reported by
              </h4>
              <ul className="mt-1.5 space-y-1">
                {article.related.map((r) => (
                  <li key={r.id} className="text-xs">
                    <a href={r.url} target="_blank" rel="noopener noreferrer" className="hover:underline" style={{ color: "var(--mf-accent)" }}>
                      {r.source}
                    </a>
                    <span style={{ color: "var(--mf-muted)" }}>
                      {" "}
                      · {formatRelativeTime(r.published_at)}: {r.title}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {article.keywords && article.keywords.length > 0 && (
            <div className="mt-6 flex flex-wrap gap-1.5">
              {article.keywords.map((kw) => (
                <span key={kw} className="rounded-md border px-2 py-0.5 text-[0.7rem] font-medium" style={{ borderColor: "var(--mf-border)", color: "var(--mf-muted)" }}>
                  #{kw}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="mt-8 border-t pt-4" style={{ borderColor: "var(--mf-border)" }}>
          <a
            href={article.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex w-full items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold shadow-sm transition-opacity hover:opacity-90"
            style={{ background: "var(--mf-accent)", color: "#ffffff" }}
          >
            <span>Open original article</span>
            <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
              <polyline points="15 3 21 3 21 9" />
              <line x1="10" y1="14" x2="21" y2="3" />
            </svg>
          </a>
          <p className="mt-2 text-center text-[0.68rem]" style={{ color: "var(--mf-muted)" }}>
            Direct link to {domain || article.source} · Opens in a new tab{paywall ? ` · ${paywall}` : ""}
          </p>
        </div>
      </div>
    </div>
  );
}
