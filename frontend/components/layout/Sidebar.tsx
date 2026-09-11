"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { getMetaStatus } from "@/lib/api/meta";
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

const NAV_ITEMS = [
  { href: "/", label: "Overview" },
  { href: "/screener", label: "Scheme Screener" },
  { href: "/compare", label: "Compare & Simulate" },
  { href: "/leaders", label: "Leaders & Laggards" },
  { href: "/quant", label: "Quantitative MF Analysis" },
  { href: "/admin", label: "Data Management" },
  { href: "/portfolio", label: "Portfolio Suggestion" },
];

export function Sidebar({ pageContext }: { pageContext?: { label: string; value: string; sub?: string } }) {
  const pathname = usePathname();
  const { preset, start, end } = useDateRangeStore();
  const [telemetryOpen, setTelemetryOpen] = useState(false);

  const { data: status } = useQuery({
    queryKey: ["meta-status"],
    queryFn: getMetaStatus,
    refetchInterval: 60_000,
  });

  const pillLevel = status?.is_stale ? "warning" : "success";
  const pillLabel = status?.is_stale
    ? `Sync Pending — expected ${status?.expected_date ?? "today"}`
    : `Live Feed Active — synced to ${status?.max_date ?? "..."}`;

  return (
    <aside className="flex w-64 flex-shrink-0 flex-col gap-3 border-r p-4" style={{ borderColor: "var(--mf-border)" }}>
      <nav className="flex flex-col gap-1">
        {NAV_ITEMS.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className="rounded-lg px-3 py-1.5 text-sm"
            style={{
              background: pathname === item.href ? "var(--mf-accent-bg)" : "transparent",
              color: pathname === item.href ? "var(--mf-accent)" : "var(--mf-fg)",
              fontWeight: pathname === item.href ? 600 : 400,
            }}
          >
            {item.label}
          </Link>
        ))}
      </nav>

      <div className="mt-2 flex flex-col gap-2">
        <div className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>
          🇮🇳 AMFI Analytics
        </div>
        <StatusPill label={pillLabel} level={pillLevel} />

        <div className="filter-box">
          <div className="text-[0.68rem] font-bold uppercase tracking-wide" style={{ color: "var(--mf-muted)" }}>
            Active Time Horizon
          </div>
          <div className="mt-0.5 font-bold">{preset}</div>
          {start && end && (
            <div className="mt-0.5 text-[0.78rem]" style={{ color: "var(--mf-muted)" }}>
              {start} → {end}
            </div>
          )}
        </div>

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

      <div className="mt-auto border-t pt-2" style={{ borderColor: "var(--mf-border)" }}>
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
            <div>• Coverage: {status.min_date} to {status.max_date}</div>
            <div>• Storage: {status.file_size_mb} MB</div>
            <div>• Engine: DuckDB (Columnar OLAP)</div>
          </div>
        )}
      </div>
    </aside>
  );
}
