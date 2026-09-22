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
              type: "scatter", mode: "lines", name: data.benchmark?.scheme_name ?? "Benchmark", x: s.dates, y: s.benchmark_index,
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
        y: attr.map((h) => (h.scheme_name ?? String(h.scheme_code)).slice(0, 48)),
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
    const mf = data.monthly_flows ?? [];
    const flowFig = {
      data: [
        { type: "bar", name: "Invested", x: mf.map((m) => m.month), y: mf.map((m) => m.invested), marker: { color: SERIES_1 }, hovertemplate: "Invested ₹%{y:,.0f}<extra></extra>" },
        { type: "bar", name: "Withdrawn", x: mf.map((m) => m.month), y: mf.map((m) => -m.withdrawn), marker: { color: SERIES_2 }, hovertemplate: "Withdrawn ₹%{customdata:,.0f}<extra></extra>", customdata: mf.map((m) => m.withdrawn) },
      ],
      layout: {
        height: 260, barmode: "relative", bargap: 0.25, legend: { orientation: "h", y: 1.15 }, margin: { t: 30, l: 70 },
        yaxis: { ...AXIS, tickprefix: "₹", tickformat: ",.0f", zeroline: true }, xaxis: { ...AXIS, type: "category" },
      },
    };
    return { valueFig, twrFig, attrFig, flowFig };
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
            Growth of 100 vs {data.benchmark?.scheme_name ?? "benchmark"}
            <FormulaTooltip
              label="Time-weighted return (TWR)"
              formula={"rₜ = (Vₜ − Fₜ) / Vₜ₋₁ − 1"}
              description="Removes the effect of when you added or withdrew money, so it measures how your funds performed. Directly comparable with an index."
            />
          </h3>
          <p className="text-xs" style={{ color: "var(--mf-muted)" }}>Both lines start at 100 on your first investment date.</p>
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
          <h3 className="text-base font-bold">Money in and out, by month</h3>
          <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
            Switches between your own funds are excluded. Total invested {formatInr((data.monthly_flows ?? []).reduce((a, m) => a + m.invested, 0))}.
          </p>
          <PlotlyChart figure={figs.flowFig} />
        </section>
      </div>
    </div>
  );
}
