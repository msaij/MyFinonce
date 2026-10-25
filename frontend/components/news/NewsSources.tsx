"use client";

import React from "react";
import type { FeedHealth, NewsSourcesResponse } from "./types";
import { feedStateStyle, formatIstTime, formatRelativeTime, paywallNote } from "@/lib/newsFormat";

/** Per-feed health: which sources answered, which are stale or failing, and why. A feed
 * that answers with nothing usable shows as failing, so a dead source is visible. */
export function NewsSources({ data, isLoading, error }: { data?: NewsSourcesResponse; isLoading: boolean; error?: Error | null }) {
  if (isLoading && !data) {
    return <p className="text-[0.7rem]" style={{ color: "var(--mf-muted)" }}>Loading source status…</p>;
  }
  if (!data) {
    return (
      <p className="text-[0.7rem]" style={{ color: "var(--mf-danger)" }}>
        Could not load source status{error ? `: ${error.message}` : "."}
      </p>
    );
  }
  return (
    <div className="rounded-lg border p-2.5" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)" }}>
      <p className="text-[0.7rem]" style={{ color: "var(--mf-muted)" }}>
        {data.feeds_ok} of {data.feeds_total} sources OK
        {data.feeds_stale ? ` · ${data.feeds_stale} stale` : ""}
        {data.feeds_failing ? ` · ${data.feeds_failing} failing` : ""}
        {data.feeds_pending ? ` · ${data.feeds_pending} not checked since the backend started` : ""}
        {data.next_sweep_at ? ` · next fetch ${formatIstTime(data.next_sweep_at)}` : data.daemon_running ? "" : " · background fetching is off"}
      </p>
      <ul className="mt-2 max-h-[16rem] space-y-1.5 overflow-y-auto pr-1">
        {data.feeds.map((f) => (
          <SourceRow key={f.name} feed={f} />
        ))}
      </ul>
    </div>
  );
}

function SourceRow({ feed }: { feed: FeedHealth }) {
  const state = feedStateStyle(feed.state);
  const newest = feed.newest_item_at ?? feed.stored_newest_at;
  const detail =
    feed.state === "failing" || feed.state === "backoff"
      ? [
          feed.last_error,
          feed.consecutive_failures > 1 ? `${feed.consecutive_failures} failures in a row` : null,
          feed.backoff_until ? `next try ${formatIstTime(feed.backoff_until)}` : null,
        ]
      : [
          newest ? `newest ${formatRelativeTime(newest)}` : null,
          feed.last_attempt_at ? `${feed.items_kept} of ${feed.items_parsed} items kept` : null,
          `${feed.stored} stored`,
        ];
  const tags = [feed.official ? "primary source" : null, paywallNote(feed.paywall) || null].filter(Boolean);
  return (
    <li className="text-[0.7rem] leading-snug">
      <div className="flex items-center gap-1.5">
        <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: state.color }} aria-hidden="true" />
        <span className="font-semibold" style={{ color: "var(--mf-fg)" }}>{feed.name}</span>
        <span style={{ color: state.color }}>{state.label}</span>
      </div>
      <div className="pl-3" style={{ color: "var(--mf-muted)" }}>
        {detail.filter(Boolean).join(" · ")}
        {tags.length ? ` · ${tags.join(" · ")}` : ""}
      </div>
    </li>
  );
}
