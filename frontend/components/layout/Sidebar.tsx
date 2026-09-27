"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { NonAdviceNotice } from "@/components/shared/Disclaimer";
import { getAlertCount } from "@/lib/api/holdings";
import { getMetaStatus } from "@/lib/api/meta";
import { formatDate } from "@/lib/format";
import { useDateRangeStore } from "@/lib/stores/dateRange";
import { StatusPill } from "./StatusPill";

/**
 * Port of date_picker.py's render_sidebar_status() -- a real 5-job function
 * (brand/live-feed pill, active-time-horizon card, page-context slot,
 * quick-sync button, telemetry drawer), not an incidental widget. See the
 * migration plan's frontend architecture note on this.
 *
 * The quick "Sync Closing NAVs Now" button and daemon-start side effect are
 * intentionally NOT ported here yet -- those are write/admin actions that
 * belong with the Data Management page's admin router (Phase 9), not a
 * read-only sidebar. This shows status only for now.
 */

// A group with no title renders its links without a heading: standalone pages that belong to no domain.
const NAV_GROUPS: { title: string | null; items: { href: string; label: string }[] }[] = [
  { title: null, items: [{ href: "/calculator", label: "Calculator" }] },
  { title: "Stock Market", items: [{ href: "/nse-ipo", label: "NSE IPO" }] },
  {
    title: "Mutual Funds",
    // Market overview, then your own portfolio, then single-fund research; data upkeep last.
    items: [
      { href: "/", label: "Overview" },
      { href: "/holdings", label: "Holdings" },
      { href: "/screener", label: "Scheme Screener" },
      { href: "/compare", label: "Compare & Simulate" },
      { href: "/quant", label: "Quantitative MF Analysis" },
      { href: "/admin", label: "Data Management" },
    ],
  },
];

const isActive = (pathname: string, href: string) => pathname === href || (href !== "/" && pathname.startsWith(href));

export function Sidebar({ pageContext, showScope = true }: { pageContext?: { label: string; value: string; sub?: string }; showScope?: boolean }) {
  const pathname = usePathname();
  const { preset, start, end, planType, optionType } = useDateRangeStore();
  const [telemetryOpen, setTelemetryOpen] = useState(false);

  const { data: status } = useQuery({
    queryKey: ["meta-status"],
    queryFn: getMetaStatus,
    refetchInterval: 60_000,
  });
  // Unacknowledged holdings alerts (evaluated server-side after each NAV sync).
  const { data: alertCount } = useQuery({
    queryKey: ["holdings", "alert-count"],
    queryFn: getAlertCount,
    refetchInterval: 60_000,
  });
  const unackedAlerts = alertCount?.unacked ?? 0;

  // Before `status` resolves, `status?.is_stale` is undefined (falsy) -- without this guard
  // the pill would flash green ("Live (Closing NAVs)") during initial load on a database
  // that turns out to be stale once the query returns. Treat undefined as neutral loading.
  const pillLabel =
    status === undefined
      ? "Checking feed..."
      : status.is_stale
        ? "Feed Behind (Catch-up Needed)"
        : "Live (Closing NAVs)";
  const pillLevel =
    status === undefined ? "neutral" : status.is_stale ? "warning" : "success";

  return (
    <aside
      className="fixed inset-y-0 left-0 z-30 flex h-screen w-64 flex-col gap-3 overflow-y-auto border-r p-4 scrollbar-thin"
      style={{
        borderColor: "var(--mf-border)",
        background: "var(--mf-card-bg)",
      }}
    >
      <div className="flex items-center gap-2">
        <div className="text-xl font-bold tracking-tight">myFinonce</div>
      </div>

      <nav className="flex flex-col gap-1">
        {NAV_GROUPS.map((group, gi) => (
          <div key={group.title ?? `ungrouped-${gi}`} className={`flex flex-col gap-1 ${gi === 0 ? "" : "mt-3"}`}>
            {group.title && (
              <div className="mb-1 text-[0.68rem] font-bold uppercase tracking-wide" style={{ color: "var(--mf-muted)" }}>
                {group.title}
              </div>
            )}
            {group.items.map((item) => {
              const active = isActive(pathname, item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className="rounded-lg px-3 py-1.5 text-sm"
                  style={{
                    background: active ? "var(--mf-accent-bg)" : "transparent",
                    color: active ? "var(--mf-accent)" : "var(--mf-fg)",
                    fontWeight: active ? 600 : 400,
                  }}
                >
                  {item.label}
                  {item.href === "/holdings" && unackedAlerts > 0 && (
                    <span
                      className="ml-2 rounded-full px-1.5 py-0.5 text-[0.65rem] font-bold"
                      style={{ background: "var(--mf-danger)", color: "#fff" }}
                      aria-label={`${unackedAlerts} unread holdings alert${unackedAlerts === 1 ? "" : "s"}`}
                    >
                      {unackedAlerts}
                    </span>
                  )}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="mt-2 flex flex-col gap-2">
        <StatusPill label={pillLabel} level={pillLevel} />

        {showScope && (
        <div className="filter-box">
          <div className="text-[0.68rem] font-bold uppercase tracking-wide" style={{ color: "var(--mf-muted)" }}>
            Active Scope
          </div>
          <div className="mt-0.5 font-bold">{preset}</div>
          {start && end && (
            <div className="mt-0.5 text-[0.78rem]" style={{ color: "var(--mf-muted)" }}>
              {formatDate(start)} → {formatDate(end)}
            </div>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-1 border-t pt-1.5 text-[0.72rem]" style={{ borderColor: "var(--mf-border)" }}>
            <span className="font-semibold" style={{ color: "var(--mf-muted)" }}>Plan:</span>
            <span className="font-medium" style={{ color: "var(--mf-accent)" }}>{planType}</span>
            <span className="mx-0.5" style={{ color: "var(--mf-muted)" }}>•</span>
            <span className="font-semibold" style={{ color: "var(--mf-muted)" }}>Option:</span>
            <span className="font-medium" style={{ color: "var(--mf-accent)" }}>{optionType}</span>
          </div>
        </div>
        )}

        {pageContext && (
          <div className="filter-box">
            <div className="text-[0.68rem] font-bold uppercase tracking-wide" style={{ color: "var(--mf-muted)" }}>
              {pageContext.label}
            </div>
            <div className="mt-0.5 font-semibold">{pageContext.value}</div>
            {pageContext.sub && (
              <div className="mt-0.5 text-[0.75rem]" style={{ color: "var(--mf-muted)" }}>
                {pageContext.sub}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="mt-auto">
        <NonAdviceNotice />
      </div>

      <div className="border-t pt-2" style={{ borderColor: "var(--mf-border)" }}>
        <button
          type="button"
          className="w-full text-left text-xs font-semibold"
          style={{ color: "var(--mf-muted)" }}
          onClick={() => setTelemetryOpen((v) => !v)}
        >
          {telemetryOpen ? "▾" : "▸"} 💾 Database Telemetry
        </button>
        {telemetryOpen && status && (
          <div className="mt-2 flex flex-col gap-1 text-xs" style={{ color: "var(--mf-muted)" }}>
            <div>• Tracked Schemes: {status.schemes_count.toLocaleString()}</div>
            <div>• Historical NAVs: {status.nav_count.toLocaleString()}</div>
            <div>• AMCs: {status.amc_count.toLocaleString()}</div>
            {status.ter_official_schemes !== undefined && status.ter_official_schemes > 0 && (
              <div>• Official TER Schemes: {status.ter_official_schemes.toLocaleString()}</div>
            )}
            {/* One row per change in a scheme's TER, not per daily disclosure: AMFI
                republishes identical figures every day and those collapse into a single
                period, so this is far smaller than the number of files fetched. */}
            {status.ter_records_count !== undefined && status.ter_records_count > 0 && (
              <div>• TER Periods Stored: {status.ter_records_count.toLocaleString()}</div>
            )}
            <div>
              • Coverage:{" "}
              {status.min_date && status.max_date
                ? `${formatDate(status.min_date)} to ${formatDate(status.max_date)}`
                : "No data yet"}
            </div>
            <div>• Storage: {status.file_size_mb} MB</div>
            <div>• Engine: PostgreSQL 16</div>
          </div>
        )}
      </div>
    </aside>
  );
}
