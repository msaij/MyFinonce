"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo } from "react";

import { Banner } from "@/components/shared/Banner";
import { DataTable, type ColumnConfig } from "@/components/shared/DataTable";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { StatCard } from "@/components/shared/StatCard";
import { getPositionDetail, type PortfolioKey } from "@/lib/api/holdings";
import { formatDate, formatInr, formatSignedPct, toneOf } from "@/lib/format";
import { TXN_TYPE_LABELS, formatSignedInr, formatUnits, positionBadges, xirrReason } from "@/lib/holdings";

const INFLOW = new Set(["BUY", "SIP", "SWITCH_IN", "DIVIDEND_REINVEST"]);

const LOT_COLUMNS: ColumnConfig[] = [
  { key: "date", label: "Bought", format: "date" },
  { key: "units", label: "Units left", render: (_r, v) => formatUnits(v as number) },
  { key: "cost_nav", label: "Cost NAV", format: "number", decimals: 4 },
  { key: "cost", label: "Cost basis", format: "inr" },
];

const TXN_COLUMNS: ColumnConfig[] = [
  { key: "trade_date", label: "Date", format: "date" },
  { key: "txn_type", label: "Type", render: (_r, v) => TXN_TYPE_LABELS[v as keyof typeof TXN_TYPE_LABELS] ?? String(v) },
  { key: "amount", label: "Amount", render: (_r, v) => formatInr(Number(v)) },
  { key: "effective_units", label: "Units", render: (r, v) => `${formatUnits(v as number)}${Number(r.units_scale) !== 1 ? " (split-adj.)" : ""}` },
  { key: "nav", label: "NAV", render: (r, v) => `${Number(v).toFixed(4)}${r.nav_source === "user" ? " (yours)" : ""}` },
  { key: "balance_units", label: "Balance", render: (_r, v) => formatUnits(v as number) },
];

export function PositionDrawer({ pid, schemeCode, onClose }: { pid: PortfolioKey; schemeCode: number | null; onClose: () => void }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["holdings", "position", pid, schemeCode],
    queryFn: () => getPositionDetail(pid, schemeCode!),
    enabled: schemeCode !== null,
  });

  const figure = useMemo(() => {
    if (!data) return null;
    const buys = data.transactions.filter((t) => INFLOW.has(t.txn_type));
    const sells = data.transactions.filter((t) => !INFLOW.has(t.txn_type) && t.txn_type !== "DIVIDEND_PAYOUT");
    const marker = (rows: typeof buys, name: string, color: string, symbol: string) => ({
      type: "scatter",
      mode: "markers",
      name,
      x: rows.map((t) => t.trade_date),
      // Plot at AMFI's (split-adjusted) scale so markers sit on the line even for pre-split NAVs.
      y: rows.map((t) => Number(t.nav) / (Number(t.units_scale) || 1)),
      text: rows.map((t) => `${TXN_TYPE_LABELS[t.txn_type]}<br>${formatInr(Number(t.amount))}`),
      hovertemplate: "%{text}<br>NAV %{y:.4f}<extra></extra>",
      marker: { color, size: 10, symbol, line: { color: "#ffffff", width: 1 } },
    });
    return {
      data: [
        {
          type: "scatter",
          mode: "lines",
          name: "NAV",
          x: data.nav_series.map((p) => p.date),
          y: data.nav_series.map((p) => p.nav),
          line: { color: "#1d4ed8", width: 1.8 },
          hovertemplate: "%{x}<br>NAV %{y:.4f}<extra></extra>",
        },
        marker(buys, "Bought", "#047857", "triangle-up"),
        marker(sells, "Sold / switched out", "#b91c1c", "triangle-down"),
      ],
      layout: { height: 300, legend: { orientation: "h", y: -0.2 }, margin: { t: 10, l: 55 } },
    };
  }, [data]);

  if (schemeCode === null) return null;
  const p = data?.position;

  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true" aria-label="Holding detail">
      <button type="button" aria-label="Close" className="absolute inset-0 cursor-default" style={{ background: "rgba(15,23,42,0.35)" }} onClick={onClose} />
      <div className="relative flex h-full w-full max-w-3xl flex-col gap-4 overflow-y-auto p-6 shadow-xl" style={{ background: "var(--mf-bg)", color: "var(--mf-fg)" }}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold">{p?.scheme_name ?? `Scheme ${schemeCode}`}</h2>
            <div className="text-xs" style={{ color: "var(--mf-muted)" }}>
              {[p?.fund_house, p?.category, p?.plan_type, p?.option_type].filter(Boolean).join(" · ")}
            </div>
            {p && positionBadges(p).length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {positionBadges(p).map((b) => (
                  <span key={b} className="rounded-full border px-2 py-0.5 text-[0.7rem] font-semibold" style={{ borderColor: "var(--mf-border)" }}>
                    {b}
                  </span>
                ))}
              </div>
            )}
          </div>
          <button type="button" onClick={onClose} className="rounded px-2 py-1 text-sm" style={{ color: "var(--mf-muted)" }}>
            ✕
          </button>
        </div>

        {isLoading && <div style={{ color: "var(--mf-muted)" }}>Loading…</div>}
        {isError && <Banner level="danger">{(error as Error).message}</Banner>}

        {p && (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatCard title="Current value" value={formatInr(p.current_value)} sub={p.latest_date ? `NAV ${p.latest_nav?.toFixed(4)} on ${formatDate(p.latest_date)}` : ""} />
            <StatCard title="Units" value={formatUnits(p.units)} sub={p.avg_cost_nav ? `Avg cost ${p.avg_cost_nav.toFixed(4)}` : ""} />
            <StatCard title="Unrealised gain" value={formatSignedInr(p.unrealised_gain)} tone={toneOf(p.unrealised_gain)} sub={formatSignedPct(p.unrealised_pct, 2)} subTone={toneOf(p.unrealised_pct)} />
            <StatCard
              title="XIRR"
              value={p.xirr_pct !== null ? formatSignedPct(p.xirr_pct, 2) : "—"}
              tone={toneOf(p.xirr_pct)}
              sub={p.xirr_pct === null ? xirrReason(p.xirr_note) : "Money-weighted, annualised"}
            />
            <StatCard title="Realised gain" value={formatSignedInr(p.realised_gain)} tone={toneOf(p.realised_gain)} sub="FIFO, on units sold" />
            <StatCard title="Dividends" value={formatInr(p.dividend_income)} />
            <StatCard title="Invested (gross)" value={formatInr(p.total_invested)} sub={`Redeemed ${formatInr(p.total_redeemed)}`} />
            <StatCard title="TER" value={p.expense_ratio !== null ? `${Number(p.expense_ratio).toFixed(2)}%` : "—"} sub={p.ter_status ?? ""} />
          </div>
        )}

        {figure && data && data.nav_series.length > 0 && (
          <div className="rounded-lg border p-2" style={{ borderColor: "var(--mf-border)" }}>
            <PlotlyChart figure={figure} />
          </div>
        )}

        {data && data.lots.length > 0 && (
          <div>
            <h3 className="mb-2 text-base font-bold">Open lots (FIFO order)</h3>
            <DataTable columns={LOT_COLUMNS} rows={data.lots as unknown as Record<string, unknown>[]} keyField="txn_id" />
          </div>
        )}

        {data && (
          <div>
            <h3 className="mb-2 text-base font-bold">Transactions</h3>
            <DataTable columns={TXN_COLUMNS} rows={data.transactions as unknown as Record<string, unknown>[]} keyField="id" />
          </div>
        )}

        <div className="flex gap-4 text-sm">
          <Link href={`/scheme/${schemeCode}`} style={{ color: "var(--mf-accent)" }}>
            Scheme dossier →
          </Link>
          <Link href={`/quant?scheme_code=${schemeCode}`} style={{ color: "var(--mf-accent)" }}>
            Quant analysis →
          </Link>
        </div>
      </div>
    </div>
  );
}
