"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { getMetaFilters, getMetaStatus } from "@/lib/api/meta";
import { formatDate } from "@/lib/format";
import { useUrlSync } from "@/lib/hooks";
import { triggerText } from "@/lib/syncJobs";
import {
  PRESET_OPTIONS,
  Preset,
  PLAN_TYPE_OPTIONS,
  PlanType,
  OPTION_TYPE_OPTIONS,
  OptionType,
  useDateRangeStore,
} from "@/lib/stores/dateRange";

/**
 * Global controls component: Plan Type, Option Type, and active date range window.
 * Placed in the top header (AppShell) so users can configure filters once to apply
 * globally across all pages (Overview, Screener, Compare, Quant).
 * Synchronizes with URL query parameters for bookmarking and institutional sharing.
 */
export function DateRangePicker() {
  const searchParams = useSearchParams();
  const {
    preset,
    start,
    end,
    planType,
    optionType,
    setPreset,
    setCustomRange,
    setPlanType,
    setOptionType,
    syncBounds,
  } = useDateRangeStore();

  // Rehydrate initial state from URL parameters on first mount if specified. The value is
  // passed through rather than checked against a fixed list: the valid set now comes from
  // the data (see planOptions/optionOptions below), and an unknown value simply returns
  // nothing rather than being silently ignored here.
  useEffect(() => {
    if (!searchParams) return;
    const urlPlan = searchParams.get("plan") || searchParams.get("plan_type");
    if (urlPlan) setPlanType(urlPlan as PlanType);
    const urlOption = searchParams.get("option") || searchParams.get("option_type");
    if (urlOption) setOptionType(urlOption as OptionType);
    const urlStart = searchParams.get("start");
    const urlEnd = searchParams.get("end");
    if (urlStart && urlEnd && /^\d{4}-\d{2}-\d{2}$/.test(urlStart) && /^\d{4}-\d{2}-\d{2}$/.test(urlEnd)) {
      setCustomRange(urlStart, urlEnd);
    } else {
      const urlPreset = searchParams.get("preset");
      if (urlPreset && (PRESET_OPTIONS as readonly string[]).includes(urlPreset)) {
        setPreset(urlPreset as Preset);
      }
    }
  }, []);

  // Serialize active global filters to URL query parameters for sharing and bookmarking
  useUrlSync({
    plan: planType !== "All Plans" ? planType : undefined,
    option: optionType !== "All Options" ? optionType : undefined,
    preset: preset !== "Past 90 Days (3M)" && preset !== "Custom Range" ? preset : undefined,
    start: preset === "Custom Range" ? start : undefined,
    end: preset === "Custom Range" ? end : undefined,
  });

  const { data: status } = useQuery({
    queryKey: ["meta-status"],
    queryFn: getMetaStatus,
    // Faster while data is updating, so the line below clears soon after the sync ends.
    refetchInterval: (q) => (q.state.data?.sync_activity || q.state.data?.backfill_running ? 10_000 : 60_000),
  });
  const activity = status?.sync_activity;

  useEffect(() => {
    if (!status) return;
    if (status.min_date && status.max_date) {
      syncBounds(status.min_date, status.max_date, false);
      return;
    }
    const today = new Date().toISOString().slice(0, 10);
    syncBounds(today, today, true);
  }, [status, syncBounds]);

  // The values the database actually holds, so every scheme is reachable by some choice.
  const { data: filters } = useQuery({ queryKey: ["meta-filters"], queryFn: () => getMetaFilters() });
  const planOptions = filters?.plans?.length ? filters.plans : [...PLAN_TYPE_OPTIONS];
  const optionOptions = filters?.options?.length ? filters.options : [...OPTION_TYPE_OPTIONS];

  const span = start && end ? Math.round((new Date(end).getTime() - new Date(start).getTime()) / 86400000) : 0;
  const isBeforeMin = status?.min_date && start && start < status.min_date;

  return (
    <div className="flex flex-col items-end gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        {/* Global Plan Type Selector */}
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>
            Plan:
          </span>
          <select
            className="rounded-lg border px-2.5 py-1.5 text-xs font-medium focus:outline-none focus:ring-2 focus:ring-blue-500/20"
            style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
            value={planType}
            onChange={(e) => setPlanType(e.target.value as PlanType)}
            title="Global Plan Type filter: applies across all pages"
          >
            {planOptions.map((opt) => (
              <option key={opt} value={opt} className="bg-white text-slate-900">
                {opt}
              </option>
            ))}
          </select>
        </div>

        {/* Global Option Type Selector */}
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>
            Option:
          </span>
          <select
            className="rounded-lg border px-2.5 py-1.5 text-xs font-medium focus:outline-none focus:ring-2 focus:ring-blue-500/20"
            style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
            value={optionType}
            onChange={(e) => setOptionType(e.target.value as OptionType)}
            title="Global Option Type filter: applies across all pages"
          >
            {optionOptions.map((opt) => (
              <option key={opt} value={opt} className="bg-white text-slate-900">
                {opt}
              </option>
            ))}
          </select>
        </div>

        {/* Vertical Divider */}
        <div className="hidden h-5 w-px sm:block" style={{ background: "var(--mf-border)" }} />

        {/* Date Window Controls */}
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>
            Window:
          </span>
          <select
            className="rounded-lg border px-2 py-1.5 text-xs font-medium focus:outline-none focus:ring-2 focus:ring-blue-500/20"
            style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
            value={preset}
            onChange={(e) => setPreset(e.target.value as Preset)}
            title="Date range preset window"
          >
            {PRESET_OPTIONS.map((p) => (
              <option key={p} value={p} className="bg-white text-slate-900">
                {p}
              </option>
            ))}
          </select>
          <input
            type="date"
            className="rounded-lg border px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-blue-500/20"
            style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
            value={start}
            onChange={(e) => {
              const newStart = e.target.value;
              setCustomRange(newStart, end && newStart > end ? newStart : end);
            }}
          />
          <span className="text-xs font-medium" style={{ color: "var(--mf-muted)" }}>to</span>
          <input
            type="date"
            className="rounded-lg border px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-blue-500/20"
            style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
            value={end}
            onChange={(e) => {
              const newEnd = e.target.value;
              setCustomRange(start && newEnd < start ? newEnd : start, newEnd);
            }}
          />
        </div>
      </div>

      {/* Status row: how current the data is, and -- beside it -- whether it is updating now. */}
      <div className="flex flex-wrap items-center justify-end gap-x-3 gap-y-1 text-[0.72rem]">
        <span style={{ color: isBeforeMin || status?.is_stale ? "var(--mf-warning)" : "var(--mf-muted)" }}>
          {isBeforeMin ? (
            <>
              ⚠️ Selected: {formatDate(start)} to {formatDate(end)} ({span}D) | Local data begins: {formatDate(status?.min_date)}
            </>
          ) : status?.is_stale ? (
            // The app's one NAV-freshness signal: it says so when AMFI has published newer NAVs.
            <>
              ⚠️ NAVs only through {formatDate(status.max_date)}; AMFI has published up to{" "}
              {status.expected_date ? formatDate(status.expected_date) : "a later day"} ·{" "}
              <Link href="/admin" className="font-semibold underline">
                catch up in Data Management
              </Link>{" "}
              | Active: {formatDate(start)} to {formatDate(end)} ({span}D)
            </>
          ) : (
            <>
              🟢 Synced through {formatDate(status?.max_date)} | Active: {formatDate(start)} to {formatDate(end)} ({span}D)
            </>
          )}
        </span>

        {(activity || status?.backfill_running) && (
          <Link
            href="/admin"
            role="status"
            title={activity ? "Figures may change when it finishes. Click for details in Data Management." : "Click for progress in Data Management."}
            className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-semibold"
            style={{ borderColor: "var(--mf-accent)", background: "var(--mf-accent-bg)", color: "var(--mf-accent)" }}
          >
            <span className="inline-block h-2.5 w-2.5 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden />
            {activity ? (
              <>
                Updating AMFI data · {activity.label}
                {activity.trigger && activity.trigger !== "manual" ? ` (${triggerText(activity.trigger)})` : ""} · {activity.step}…
              </>
            ) : (
              <>Historical backfill running…</>
            )}
          </Link>
        )}
      </div>
    </div>
  );
}
