"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { getMetaStatus } from "@/lib/api/meta";
import { formatDate } from "@/lib/format";
import { PRESET_OPTIONS, Preset, useDateRangeStore } from "@/lib/stores/dateRange";

/**
 * Port of date_picker.py's render_top_date_picker(). Polls /api/meta/status
 * every 60s (same cadence as the original's st.fragment(run_every=60)) and
 * feeds fresh dbMin/dbMax into the store's syncBounds(), which applies the
 * live-edge-slide rule -- see lib/stores/dateRange.ts.
 */
export function DateRangePicker() {
  const { preset, start, end, setPreset, setCustomRange, syncBounds } = useDateRangeStore();

  const { data: status } = useQuery({
    queryKey: ["meta-status"],
    queryFn: getMetaStatus,
    refetchInterval: 60_000,
  });

  useEffect(() => {
    if (!status) return;
    if (status.min_date && status.max_date) {
      syncBounds(status.min_date, status.max_date, false);
      return;
    }
    // Empty database (no sync has run yet): min_date/max_date are both null, so there's
    // no real bound to seed from. Without this fallback the store never seeds at all --
    // start/end stay "" forever, silently blocking every page whose queries gate on
    // `enabled: !!start && !!end` (Overview's KPIs/trend chart, Leaders, ...). Seed with
    // today's date as a placeholder bound, marked `isPlaceholder: true` so that once a
    // real sync lands, syncBounds() forces a fresh reseed even if the real max_date
    // coincidentally equals today (very likely right after a fresh sync) -- plain
    // value-equality alone can't tell "nothing changed" from "the placeholder happened
    // to match", which silently left the window stuck at a degenerate 0-day range the
    // first time this was tested against a real, just-synced database.
    const today = new Date().toISOString().slice(0, 10);
    syncBounds(today, today, true);
  }, [status, syncBounds]);

  const span = start && end ? Math.round((new Date(end).getTime() - new Date(start).getTime()) / 86400000) : 0;
  const isBeforeMin = status?.min_date && start && start < status.min_date;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex gap-2">
        <select
          className="rounded-lg border px-2 py-1.5 text-sm"
          style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
          value={preset}
          onChange={(e) => setPreset(e.target.value as Preset)}
        >
          {PRESET_OPTIONS.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <input
          type="date"
          className="rounded-lg border px-2 py-1.5 text-sm"
          style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
          value={start}
          onChange={(e) => setCustomRange(e.target.value, end)}
        />
        <input
          type="date"
          className="rounded-lg border px-2 py-1.5 text-sm"
          style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
          value={end}
          onChange={(e) => setCustomRange(start, e.target.value)}
        />
      </div>
      <div className="text-xs" style={{ color: isBeforeMin ? "var(--mf-warning)" : "var(--mf-muted)" }}>
        {isBeforeMin ? (
          <>
            ⚠️ Selected: {formatDate(start)} to {formatDate(end)} ({span}D) | Local data begins: {formatDate(status?.min_date)}
          </>
        ) : (
          <>
            🟢 Synced through {formatDate(status?.max_date)} | Active: {formatDate(start)} to {formatDate(end)} ({span}D)
          </>
        )}
      </div>
    </div>
  );
}
