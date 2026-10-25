"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { DataTable, ColumnConfig } from "@/components/shared/DataTable";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { Banner } from "@/components/shared/Banner";
import { formatDate } from "@/lib/format";
import { NSE_NO_RETAIN, getNseIssueDetail, type NseBidRow, type NseDemandGraph, type NseDemandRow } from "@/lib/api/nseIpo";
import { fmtQty, fmtTimes } from "@/lib/nseIpo";
import type { IssueContext } from "@/lib/nseIpoSignals";
import { IpoSignals } from "./IpoSignals";

export interface SelectedIssue extends IssueContext {
  company: string;
  symbol: string;
}

const SECTIONS = ["Issue Information", "Bid Details", "Demand Graph", "Demand Data"] as const;
type Section = (typeof SECTIONS)[number];

function Toggle<T extends string>({ options, value, onChange }: { options: readonly T[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((o) => (
        <button
          key={o}
          type="button"
          onClick={() => onChange(o)}
          className="rounded-full border px-3 py-1 text-xs font-semibold"
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

const ExtLink = ({ href, children }: { href: string; children: React.ReactNode }) => (
  <a href={href} target="_blank" rel="noopener noreferrer" className="font-semibold underline" style={{ color: "var(--mf-accent)" }}>
    {children}
  </a>
);

// Sub-rows ("1(a)", "2.1(b)") sit under their category, as on NSE.
const isSubRow = (sr: string | null) => !!sr && /\(/.test(sr);

const BID_COLUMNS: ColumnConfig[] = [
  { key: "sr_no", label: "Sr. No.", sortable: false, render: (_r, v) => (v == null ? "" : String(v)) },
  {
    key: "category",
    label: "Category",
    sortable: false,
    render: (r) => (
      <span
        className={r.category === "Total" ? "font-bold" : ""}
        style={{ paddingLeft: isSubRow(r.sr_no as string | null) ? "1.25rem" : 0, whiteSpace: "normal", display: "inline-block", maxWidth: "26rem" }}
      >
        {String(r.category)}
      </span>
    ),
  },
  { key: "shares_offered", label: "No. of shares offered / reserved", sortable: false, render: (_r, v) => fmtQty(v as number | null) },
  { key: "shares_bid", label: "No. of shares bid for", sortable: false, render: (_r, v) => fmtQty(v as number | null) },
  { key: "subscription_times", label: "No. of times of total meant for the category", sortable: false, render: (_r, v) => fmtTimes(v as number | null) },
];

const DEMAND_COLUMNS: ColumnConfig[] = [
  { key: "price", label: "Price (Rs)", sortable: false },
  { key: "cumulative_qty", label: "Cumulative quantity", sortable: false, render: (_r, v) => fmtQty(v as number | null) },
  { key: "timestamp", label: "As on", sortable: false },
];

function BidTable({ rows }: { rows: NseBidRow[] }) {
  if (!rows.length) return <Banner level="info">No bid details published yet.</Banner>;
  return <DataTable columns={BID_COLUMNS} rows={rows.map((r, i) => ({ ...r, _k: i }))} keyField="_k" />;
}

function DemandGraph({ g }: { g: NseDemandGraph | null }) {
  const figure = useMemo(() => {
    const pts = g?.points ?? [];
    return {
      data: [
        {
          type: "bar",
          x: pts.map((p) => p.price),
          y: pts.map((p) => p.qty_lakh),
          marker: { color: "#2563EB" },
          text: pts.map((p) => (p.qty_lakh == null ? "" : String(p.qty_lakh))),
          textposition: "outside",
          cliponaxis: false,
          customdata: pts.map((p) => (p.cumulative_qty == null ? "-" : p.cumulative_qty.toLocaleString("en-IN"))),
          hovertemplate: "Price %{x}<br>Bids: %{y} lakh shares<br>Cumulative: %{customdata}<extra></extra>",
        },
      ],
      layout: {
        height: 380,
        showlegend: false,
        xaxis: { type: "category", title: { text: "Price (Rs)" } },
        yaxis: { title: { text: "No. of shares (lakh)" }, showgrid: true },
        margin: { t: 30 },
      },
    };
  }, [g]);

  if (!g) return <Banner level="info">No demand graph published for this issue.</Banner>;
  const stat = (label: string, value: string) => (
    <div className="rounded-lg border p-2" style={{ borderColor: "var(--mf-border)" }}>
      <div className="text-[0.7rem] font-semibold uppercase" style={{ color: "var(--mf-muted)" }}>{label}</div>
      <div className="text-sm font-bold">{value}</div>
    </div>
  );
  return (
    <div className="space-y-3">
      <div>
        <div className="font-semibold">{g.heading}</div>
        <div className="text-xs" style={{ color: "var(--mf-muted)" }}>{g.subheading}</div>
        {g.timestamp && <div className="text-xs" style={{ color: "var(--mf-muted)" }}>{g.timestamp}</div>}
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        {stat("Total issue size", fmtQty(g.total_issue_size))}
        {stat("Total bids received", fmtQty(g.total_bids))}
        {stat("Bids at cut-off", fmtQty(g.bids_at_cutoff))}
        {stat("Times subscribed", fmtTimes(g.times_subscribed))}
      </div>
      {g.points.length ? <PlotlyChart figure={figure} /> : <Banner level="info">No bids plotted yet.</Banner>}
      {g.points.length > 0 && (
        <DataTable
          columns={[
            { key: "price", label: "Price (Rs)", sortable: false },
            { key: "qty_lakh", label: "Bids at this price (lakh shares)", sortable: false, render: (_r, v) => (v == null ? "-" : String(v)) },
            { key: "cumulative_qty", label: "Cumulative demand (shares)", sortable: false, render: (_r, v) => fmtQty(v as number | null) },
          ]}
          rows={g.points.map((p) => ({ ...p }))}
          keyField="price"
        />
      )}
      <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
        <b>Note:</b> {g.note} {g.graph_logic_url && <ExtLink href={g.graph_logic_url}>Graph logic</ExtLink>}
      </p>
    </div>
  );
}

function DemandData({ rows }: { rows: NseDemandRow[] }) {
  if (!rows.length) return <Banner level="info">No demand data published for this issue.</Banner>;
  return <DataTable columns={DEMAND_COLUMNS} rows={rows} keyField="price" />;
}

/** Everything on NSE's issue-information page for one issue, in a panel over the listing. */
export function IssueDetailDrawer({ issue, onClose }: { issue: SelectedIssue; onClose: () => void }) {
  const [section, setSection] = useState<Section>("Issue Information");
  const [bidSource, setBidSource] = useState<"Consolidated Bid Details" | "NSE Bid Details">("Consolidated Bid Details");
  const [graphSource, setGraphSource] = useState<"NSE" | "All">("NSE");
  const [dataSource, setDataSource] = useState<"NSE Bid Data" | "All Exchange Bid Data">("NSE Bid Data");

  const { data, isLoading, isError, error, refetch, isFetching, dataUpdatedAt } = useQuery({
    queryKey: ["nse-ipo-detail", issue.symbol, issue.series],
    queryFn: () => getNseIssueDetail(issue.symbol, issue.series),
    ...NSE_NO_RETAIN,
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" style={{ background: "rgba(15, 23, 42, 0.45)" }} onClick={onClose}>
      <div
        className="h-full w-full max-w-5xl overflow-y-auto p-6 shadow-xl"
        style={{ background: "var(--mf-bg)", color: "var(--mf-fg)" }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={`${issue.company} issue information`}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold">{data?.heading || issue.company}</h2>
            <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
              {issue.symbol}
              {issue.series ? ` · ${issue.series}` : ""}
              {dataUpdatedAt ? ` · fetched from NSE at ${new Date(dataUpdatedAt).toLocaleTimeString("en-IN")}` : ""}
            </p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => refetch()}
              disabled={isFetching}
              className="rounded-lg border px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
              style={{ borderColor: "var(--mf-border)" }}
            >
              {isFetching ? "Refreshing..." : "Refresh"}
            </button>
            <button type="button" onClick={onClose} className="rounded-lg border px-3 py-1.5 text-xs font-semibold" style={{ borderColor: "var(--mf-border)" }}>
              Close ✕
            </button>
          </div>
        </div>

        {data && (
          <div className="mt-4">
            <IpoSignals detail={data} context={issue} />
          </div>
        )}

        <div className="mt-4 flex flex-wrap gap-2 border-b pb-2" style={{ borderColor: "var(--mf-border)" }}>
          {SECTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSection(s)}
              className="rounded-full px-4 py-1.5 text-sm font-semibold"
              style={{
                background: section === s ? "var(--mf-accent)" : "var(--mf-card-bg)",
                color: section === s ? "white" : "var(--mf-fg)",
                border: "1px solid var(--mf-border)",
              }}
            >
              {s}
            </button>
          ))}
        </div>

        <div className="mt-4">
          {isLoading ? (
            <p className="text-sm" style={{ color: "var(--mf-muted)" }}>Fetching issue details from NSE...</p>
          ) : isError || !data ? (
            <Banner level="warning">Could not load this issue from NSE: {(error as Error)?.message ?? "no data"}.</Banner>
          ) : (
            <>
              {section === "Issue Information" && (
                <div className="space-y-4">
                  <Banner level="info">All Investors shall mandatorily use only Application Supported by Blocked Amount (ASBA) facility for making payments.</Banner>
                  {data.listing && (data.listing.isin || data.listing.industry || data.listing.listing_date) && (
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                      {[
                        ["ISIN", data.listing.isin],
                        ["Industry", data.listing.industry],
                        ["Listing date", data.listing.listing_date ? formatDate(data.listing.listing_date) : null],
                      ].map(([k, v]) => (
                        <div key={k} className="rounded-lg border p-2" style={{ borderColor: "var(--mf-border)" }}>
                          <div className="text-[0.7rem] font-semibold uppercase" style={{ color: "var(--mf-muted)" }}>{k}</div>
                          <div className="text-sm font-bold">{v || "-"}</div>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="overflow-x-auto rounded-lg border" style={{ borderColor: "var(--mf-border)" }}>
                    <table className="w-full text-sm">
                      <tbody>
                        {data.issue_info.map((item, i) => (
                          <tr key={`${item.title}-${i}`} style={{ borderBottom: "1px solid var(--mf-border)" }}>
                            <th className="w-1/3 px-3 py-2 text-left align-top text-xs font-bold" style={{ color: "var(--mf-muted)" }}>
                              {item.title}
                            </th>
                            <td className="px-3 py-2 align-top">{item.url ? <ExtLink href={item.url}>{item.value || item.url}</ExtLink> : item.value}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {data.notices.map((n, i) => (
                    <p key={i} className="text-xs" style={{ color: "var(--mf-muted)" }}>{n}</p>
                  ))}
                </div>
              )}

              {section === "Bid Details" && (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <Toggle options={["Consolidated Bid Details", "NSE Bid Details"] as const} value={bidSource} onChange={setBidSource} />
                    {bidSource === "Consolidated Bid Details" && data.consolidated_updated && (
                      <span className="text-xs" style={{ color: "var(--mf-muted)" }}>{data.consolidated_updated}</span>
                    )}
                  </div>
                  <BidTable rows={bidSource === "NSE Bid Details" ? data.bid_details_nse : data.bid_details_consolidated} />
                </div>
              )}

              {section === "Demand Graph" && (
                <div className="space-y-3">
                  <Toggle options={["NSE", "All"] as const} value={graphSource} onChange={setGraphSource} />
                  <DemandGraph g={graphSource === "NSE" ? data.demand_graph_nse : data.demand_graph_all} />
                </div>
              )}

              {section === "Demand Data" && (
                <div className="space-y-3">
                  <Toggle options={["NSE Bid Data", "All Exchange Bid Data"] as const} value={dataSource} onChange={setDataSource} />
                  <DemandData rows={dataSource === "NSE Bid Data" ? data.demand_data_nse : data.demand_data_all} />
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
