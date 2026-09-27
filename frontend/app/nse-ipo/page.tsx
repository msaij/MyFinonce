"use client";

import { useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { AppShell } from "@/components/layout/AppShell";
import { DataTable, ColumnConfig } from "@/components/shared/DataTable";
import { Banner } from "@/components/shared/Banner";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { IssueDetailDrawer, type SelectedIssue } from "@/components/nse-ipo/IssueDetailDrawer";
import { fmtQty, fmtTimes } from "@/lib/nseIpo";
import { NSE_NO_RETAIN, getNseCurrentIssues, getNseUpcomingIssues } from "@/lib/api/nseIpo";

/**
 * NSE IPO: the current and upcoming public issues on nseindia.com/market-data/all-upcoming-issues-ipo,
 * fetched live through the backend and never stored. Inside the app shell, but without the
 * mutual-fund date-range picker, and with no cross-links into the mutual-fund pages.
 */

const TABS = ["Current", "Upcoming Issues"] as const;
type Tab = (typeof TABS)[number];

const STATUS_COLOR: Record<string, string> = { active: "var(--mf-success)", forthcoming: "var(--mf-accent)", closed: "var(--mf-muted)" };

function Pills({ options, value, onChange }: { options: string[]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((o) => (
        <button
          key={o}
          type="button"
          onClick={() => onChange(o)}
          className="rounded-full border px-3 py-1 text-xs font-medium"
          style={{
            borderColor: o === value ? "var(--mf-accent)" : "var(--mf-border)",
            background: o === value ? "var(--mf-accent-bg)" : "transparent",
            color: o === value ? "var(--mf-accent)" : "var(--mf-fg)",
          }}
        >
          {o}
        </button>
      ))}
    </div>
  );
}

export default function NseIpoPage() {
  const [tab, setTab] = useState<Tab>("Current");
  const [selected, setSelected] = useState<SelectedIssue | null>(null);

  const current = useQuery({ queryKey: ["nse-ipo-current"], queryFn: getNseCurrentIssues, refetchInterval: 5 * 60_000, ...NSE_NO_RETAIN });
  const upcoming = useQuery({ queryKey: ["nse-ipo-upcoming"], queryFn: getNseUpcomingIssues, ...NSE_NO_RETAIN });

  const refreshing = current.isFetching || upcoming.isFetching;
  const fetchedAt = Math.max(current.dataUpdatedAt, upcoming.dataUpdatedAt);
  const refreshAll = () => {
    current.refetch();
    upcoming.refetch();
  };

  const companyCell = useCallback(
    (r: Record<string, unknown>) => (
      <button
        type="button"
        onClick={() =>
          setSelected({
            company: String(r.company),
            symbol: String(r.symbol),
            series: (r.series as string) ?? null,
            status: (r.status as string) ?? null,
            issue_start: (r.issue_start as string) ?? null,
            issue_end: (r.issue_end as string) ?? null,
            subscription_times: (r.subscription_times as number | undefined) ?? null,
            issue_size: (r.issue_size as number | undefined) ?? null,
            shares_offered: (r.shares_offered as number | undefined) ?? null,
          })
        }
        className="text-left font-semibold underline-offset-2 hover:underline"
        style={{ color: "var(--mf-accent)", whiteSpace: "normal", maxWidth: "22rem" }}
        title="Open the issue's full details"
      >
        {String(r.company)}
      </button>
    ),
    []
  );

  const statusCell = (_r: Record<string, unknown>, v: unknown) => (
    <span className="font-semibold" style={{ color: STATUS_COLOR[String(v ?? "").toLowerCase()] ?? "var(--mf-fg)" }}>{String(v ?? "-")}</span>
  );

  const currentColumns: ColumnConfig[] = [
    { key: "company", label: "Company name", render: companyCell },
    { key: "symbol", label: "Symbol" },
    { key: "series", label: "Security type" },
    { key: "price_range", label: "Price range" },
    { key: "issue_size", label: "Issue size", format: "number", render: (_r, v) => fmtQty(v as number | null), tooltip: "Shares offered in the issue." },
    { key: "issue_start", label: "Issue start date", format: "date" },
    { key: "issue_end", label: "Issue end date", format: "date" },
    { key: "status", label: "Status", render: statusCell },
    { key: "shares_offered", label: "Offered / reserved", format: "number", render: (_r, v) => fmtQty(v as number | null), tooltip: "NSE Bid Details (across all categories): shares offered or reserved." },
    { key: "shares_bid", label: "Bids", format: "number", render: (_r, v) => fmtQty(v as number | null), tooltip: "NSE Bid Details (across all categories): shares bid for on NSE." },
    { key: "subscription_times", label: "Subscription (no. of times)", format: "number", render: (_r, v) => fmtTimes(v as number | null), tooltip: "Bids divided by shares offered, on NSE. Open the issue for the consolidated figure across all exchanges and the split by investor category." },
  ];

  const upcomingColumns: ColumnConfig[] = [
    { key: "series", label: "Security type" },
    { key: "company", label: "Company", render: companyCell },
    { key: "symbol", label: "Symbol" },
    { key: "issue_start", label: "Issue start date", format: "date" },
    { key: "issue_end", label: "Issue end date", format: "date" },
    { key: "status", label: "Status", render: statusCell },
    { key: "price_range", label: "Price range" },
    { key: "issue_size", label: "Issue size", format: "number", render: (_r, v) => fmtQty(v as number | null), tooltip: "Shares offered in the issue." },
  ];

  const [upStatus, setUpStatus] = useState("All");
  const upRows = useMemo(() => upcoming.data ?? [], [upcoming.data]);
  const upStatuses = useMemo(() => ["All", ...Array.from(new Set(upRows.map((r) => r.status ?? "-")))], [upRows]);
  const upFiltered = upStatus === "All" ? upRows : upRows.filter((r) => (r.status ?? "-") === upStatus);

  const loadState = (q: { isLoading: boolean; isError: boolean; error: unknown }, what: string) =>
    q.isLoading ? (
      <p className="text-sm" style={{ color: "var(--mf-muted)" }}>Fetching {what} from NSE...</p>
    ) : q.isError ? (
      <Banner level="warning">Could not reach NSE for {what}: {(q.error as Error)?.message}. Try Refresh in a minute.</Banner>
    ) : null;

  const counts: Record<Tab, number | undefined> = { Current: current.data?.length, "Upcoming Issues": upcoming.data?.length };

  return (
    <AppShell hideDateRange>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="mf-page-title">NSE IPO</h1>
            <p className="mf-page-caption">
              Public issues on the National Stock Exchange: open for bidding now and coming up. Live from nseindia.com; click a company for its full issue
              information, bid details and demand.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {fetchedAt > 0 && (
              <span className="text-xs" style={{ color: "var(--mf-muted)" }}>
                Fetched {new Date(fetchedAt).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}
              </span>
            )}
            <button
              type="button"
              onClick={refreshAll}
              disabled={refreshing}
              className="rounded-lg border px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
              style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
            >
              {refreshing ? "Refreshing..." : "Refresh"}
            </button>
          </div>
        </div>

        <div className="mt-6 flex flex-wrap gap-2 border-b pb-2" style={{ borderColor: "var(--mf-border)" }}>
          {TABS.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className="rounded-full px-4 py-2 text-sm font-semibold"
              style={{
                background: tab === t ? "var(--mf-accent)" : "var(--mf-card-bg)",
                color: tab === t ? "white" : "var(--mf-fg)",
                border: "1px solid var(--mf-border)",
              }}
            >
              {t}
              {counts[t] != null && <span className="ml-1.5 opacity-80">({counts[t]!.toLocaleString()})</span>}
            </button>
          ))}
        </div>

        <div className="mt-5 space-y-4">
          {tab === "Current" &&
            (loadState(current, "current issues") ?? (
              <>
                <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
                  Issues open for bidding. Offered / reserved, Bids and Subscription are NSE Bid Details across all categories.
                  <FormulaTooltip label="Current issues" description="NSE refreshes bid figures through the bidding day; this page re-fetches every 5 minutes, or use Refresh." />
                </p>
                <DataTable columns={currentColumns} rows={current.data ?? []} keyField="symbol" />
              </>
            ))}

          {tab === "Upcoming Issues" &&
            (loadState(upcoming, "upcoming issues") ?? (
              <>
                <div className="flex flex-wrap items-center gap-3">
                  <Pills options={upStatuses} value={upStatus} onChange={setUpStatus} />
                  <FormulaTooltip label="Status" description="NSE's Upcoming Issues tab shows only Forthcoming issues. Its feed also carries issues now open (Active) and just closed (Closed); pick a status to narrow the list." />
                </div>
                <DataTable columns={upcomingColumns} rows={upFiltered} keyField="symbol" />
              </>
            ))}
        </div>

        <p className="mt-8 text-xs" style={{ color: "var(--mf-muted)" }}>
          Source: National Stock Exchange of India (nseindia.com), public issues. Figures are shown as NSE publishes them and are not stored by this app.
        </p>

      {selected && <IssueDetailDrawer issue={selected} onClose={() => setSelected(null)} />}
    </AppShell>
  );
}
