"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { Banner } from "@/components/shared/Banner";
import { DataTable, type ColumnConfig } from "@/components/shared/DataTable";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { getPerformance, type PortfolioKey } from "@/lib/api/holdings";
import { formatDate, formatInr, formatSignedPct } from "@/lib/format";
import { formatSignedInr } from "@/lib/holdings";
import { AXIS, GAIN, LOSS, REFERENCE, SERIES_1, SERIES_2 } from "@/lib/holdingsChart";

const pctCell = (_r: Record<string, unknown>, v: unknown) => {
  const n = v as number | null;
  if (n === null || n === undefined) return "-";
  return <span className={n > 0 ? "mf-pos" : n < 0 ? "mf-neg" : ""}>{formatSignedPct(n, 2)}</span>;
};

export function PerformancePanel({ pid }: { pid: PortfolioKey }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["holdings", "performance", pid],
    queryFn: () => getPerformance(pid),
  });

  const figs = useMemo(() => {
    if (!data || data.empty || !data.series) return null;
    const s = data.series;
    const valueFig = {
      data: [
        {
          type: "scatter", mode: "lines", name: "Current value", x: s.dates, y: s.value,
          line: { color: SERIES_1, width: 2 }, fill: "tozeroy", fillcolor: "rgba(42,120,214,0.10)",
          hovertemplate: "Value ₹%{y:,.0f}<extra></extra>",
        },
        {
          type: "scatter", mode: "lines", name: "Net invested", x: s.dates, y: s.invested,
          line: { color: REFERENCE, width: 2, dash: "dot", shape: "hv" },
          hovertemplate: "Net invested ₹%{y:,.0f}<extra></extra>",
        },
      ],
      layout: {
        height: 320, legend: { orientation: "h", y: 1.12 }, margin: { t: 30, l: 70 },
        yaxis: { ...AXIS, tickprefix: "₹", tickformat: ",.0f", rangemode: "tozero" }, xaxis: { ...AXIS },
      },
    };
    const twrFig = {
      data: [
        {
          type: "scatter", mode: "lines", name: "Your portfolio (TWR)", x: s.dates, y: s.twr_index,
          line: { color: SERIES_1, width: 2 }, hovertemplate: "Portfolio %{y:.2f}<extra></extra>",
        },
        ...(s.benchmark_index
          ? [{
              type: "scatter", mode: "lines", name: data.benchmark?.kind === "category_blend" ? "Peer-group blend" : data.benchmark?.scheme_name ?? "Benchmark", x: s.dates, y: s.benchmark_index,
              line: { color: SERIES_2, width: 2 }, hovertemplate: "Benchmark %{y:.2f}<extra></extra>",
            }]
          : []),
      ],
      layout: {
        height: 320, legend: { orientation: "h", y: 1.12 }, margin: { t: 30, l: 55 },
        yaxis: { ...AXIS, title: { text: "Growth of 100" } }, xaxis: { ...AXIS },
        shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 100, y1: 100, line: { color: REFERENCE, width: 1, dash: "dot" } }],
      },
    };
    const attr = [...(data.attribution?.holdings ?? [])].reverse();
    const attrFig = {
      data: [{
        type: "bar", orientation: "h",
        y: attr.map((h) => h.scheme_name ?? String(h.scheme_code)),
        x: attr.map((h) => h.gain),
        marker: { color: attr.map((h) => (h.gain >= 0 ? GAIN : LOSS)) },
        text: attr.map((h) => formatSignedInr(h.gain)), textposition: "outside", cliponaxis: false,
        hovertemplate: "%{y}<br>%{text}<extra></extra>",
      }],
      layout: {
        height: Math.max(180, 40 + attr.length * 34), margin: { l: 10, r: 90, t: 10 }, bargap: 0.35,
        yaxis: { automargin: true }, xaxis: { ...AXIS, tickprefix: "₹", tickformat: ",.0f", zeroline: true },
      },
    };
    // Share of money vs share of gain, one pair of bars per fund. Both are percentages of
    // a whole, so they share one axis and read directly against each other: a fund whose
    // gain bar outruns its money bar is earning more than its weight in the portfolio.
    // Largest share of the gain on top (Plotly draws horizontal bars bottom-up, hence the
    // reverse). The money bars are the neutral reference series; only the gain bars carry
    // direct labels, so the chart states its finding without a number on every mark.
    const byShare = [...(data.attribution?.holdings ?? [])]
      .sort((a, b) => b.gain_share_pct - a.gain_share_pct)
      .reverse();
    const names = byShare.map((h) => h.scheme_name ?? String(h.scheme_code));
    const vsWeight = byShare.map((h) => {
      if (!h.weight_pct) return h.gain_share_pct !== 0 ? "Fully exited — gain already banked" : "Fully exited";
      if (h.gain_share_pct <= 0) return h.gain_share_pct < 0 ? "Losing money" : "No gain yet";
      const ratio = h.gain_share_pct / h.weight_pct;
      return Math.abs(ratio - 1) < 0.05 ? "Earning in line with its weight" : `Earning ${ratio.toFixed(1)}× its weight`;
    });
    const hover = "<b>%{y}</b><br>Money %{customdata[0]:.1f}% · Gain %{customdata[1]:.1f}%<br>%{customdata[2]}<extra></extra>";
    const custom = byShare.map((h, i) => [h.weight_pct ?? 0, h.gain_share_pct, vsWeight[i]]);
    const shareFig = {
      data: [
        {
          type: "bar", orientation: "h", name: "Share of your money",
          y: names, x: byShare.map((h) => h.weight_pct ?? 0),
          marker: { color: REFERENCE, cornerradius: 4 }, customdata: custom, hovertemplate: hover,
        },
        {
          type: "bar", orientation: "h", name: "Share of your gain",
          y: names, x: byShare.map((h) => h.gain_share_pct),
          marker: { color: SERIES_1, cornerradius: 4 }, customdata: custom, hovertemplate: hover,
          text: byShare.map((h) => `${h.gain_share_pct.toFixed(1)}%`), textposition: "outside", cliponaxis: false,
        },
      ],
      layout: {
        height: Math.max(220, 60 + byShare.length * 52), barmode: "group", bargap: 0.3, bargroupgap: 0.08,
        legend: { orientation: "h", y: 1.1, traceorder: "reversed" }, margin: { l: 10, r: 60, t: 30 },
        yaxis: { automargin: true }, xaxis: { ...AXIS, ticksuffix: "%", zeroline: true },
      },
    };
    return { valueFig, twrFig, attrFig, shareFig };
  }, [data]);

  const periodColumns: ColumnConfig[] = useMemo(
    () => [
      { key: "label", label: "Period", sortable: false, render: (r, v) => `${v}${r.annualised ? " (ann.)" : ""}` },
      { key: "twr_pct", label: "Portfolio TWR", render: pctCell },
      { key: "benchmark_pct", label: "Benchmark", render: pctCell },
      { key: "excess_pct", label: "Excess", render: pctCell },
      { key: "xirr_pct", label: "XIRR", render: pctCell },
    ],
    []
  );

  if (isLoading) return <div className="mt-4 text-sm" style={{ color: "var(--mf-muted)" }}>Building your daily value history…</div>;
  if (isError) return <div className="mt-4"><Banner level="danger">{(error as Error).message}</Banner></div>;
  if (!data || data.empty || !figs) return null;
  const blend = data.benchmark?.kind === "category_blend";

  return (
    <div className="mt-4 flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <section>
          <h3 className="text-base font-bold">Value vs money invested</h3>
          <p className="text-xs" style={{ color: "var(--mf-muted)" }}>The gap between the lines is your gain in rupees.</p>
          <PlotlyChart figure={figs.valueFig} />
        </section>
        <section>
          <h3 className="flex items-center gap-1 text-base font-bold">
            Growth of 100 vs {blend ? "your funds' peer groups" : data.benchmark?.scheme_name ?? "benchmark"}
            <FormulaTooltip
              label="Time-weighted return (TWR)"
              formula={"rₜ = (Vₜ − Fₜ) / Vₜ₋₁ − 1"}
              description="Removes the effect of when you added or withdrew money, so it measures how your funds performed. Directly comparable with an index."
            />
          </h3>
          <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
            Both lines are indexed to 100 on your first investment date. The benchmark starts at exactly 100; your line starts a
            hair under it, because day one already carries the stamp duty and unit rounding on that first purchase.
            {blend && (
              <>
                {" "}Benchmark: each fund against the average of its SEBI category, weighted like your portfolio (
                {data.benchmark!.components.map((c) => `${c.category.replace(/^.* - /, "")} ${c.weight_pct.toFixed(0)}%`).join(" · ")}).
              </>
            )}
          </p>
          <PlotlyChart figure={figs.twrFig} />
        </section>
      </div>

      <section>
        <h3 className="flex items-center gap-1 text-base font-bold">
          Returns by period
          <FormulaTooltip
            label="TWR vs XIRR"
            description="TWR is how your funds did. XIRR is how your money did, given when you invested it. SIPs into a falling market can make XIRR beat TWR, and vice versa. Periods over a year are annualised."
          />
        </h3>
        <p className="text-xs" style={{ color: "var(--mf-muted)" }}>As of {formatDate(data.as_of ?? null)}. Periods longer than your history are omitted.</p>
        <div className="mt-2">
          <DataTable columns={periodColumns} rows={(data.periods ?? []) as unknown as Record<string, unknown>[]} keyField="label" />
        </div>
      </section>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <section>
          <h3 className="text-base font-bold">What drove your gain</h3>
          <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
            Rupee gain per fund since you started: value today plus money taken out, minus money put in. Total {formatSignedInr(data.attribution?.total_gain ?? 0)}.
          </p>
          <PlotlyChart figure={figs.attrFig} />
        </section>
        <section>
          <h3 className="flex items-center gap-1 text-base font-bold">
            Share of money vs share of gain
            <FormulaTooltip
              label="Share of gain"
              formula="fund gain ÷ total of all gains"
              description="Each fund's slice of what your portfolio has made, beside its slice of what your portfolio is worth today. Funds that lost money are shown as a share of the total losses instead, below zero, so the shares never run past 100% just because one fund's loss shrank the net."
            />
          </h3>
          <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
            A blue bar longer than its grey one means the fund is pulling more than its weight.
            {(data.attribution?.gross_loss ?? 0) < 0
              ? ` Gains ${formatSignedInr(data.attribution!.gross_gain)}, losses ${formatSignedInr(data.attribution!.gross_loss)}.`
              : ` Total gain ${formatInr(data.attribution?.gross_gain ?? 0)}.`}
          </p>
          <PlotlyChart figure={figs.shareFig} />
        </section>
      </div>
    </div>
  );
}
