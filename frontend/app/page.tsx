"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { AppShell } from "@/components/layout/AppShell";
import { StatCard } from "@/components/shared/StatCard";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { DataTable, ColumnConfig } from "@/components/shared/DataTable";
import { Banner } from "@/components/shared/Banner";
import { formatSignedPct, toneOf } from "@/lib/format";
import { useDebouncedValue } from "@/lib/hooks";
import { useDateRangeStore } from "@/lib/stores/dateRange";
import {
  type AssetDistRow,
  getCategoryMatrix,
  getMacroTrend,
  getOverviewKpis,
  getOverviewStats,
} from "@/lib/api/overview";

/** Small pill-style radio group, replacing every st.radio(horizontal=True) on
 * the original Overview page (plan-type filter, horizon/view/scope toggles). */
function PillRadio({
  options,
  value,
  onChange,
}: {
  options: string[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((opt) => (
        <button
          key={opt}
          type="button"
          onClick={() => onChange(opt)}
          className="rounded-full border px-3 py-1 text-xs font-medium"
          style={{
            borderColor: opt === value ? "var(--mf-accent)" : "var(--mf-border)",
            background: opt === value ? "var(--mf-accent-bg)" : "transparent",
            color: opt === value ? "var(--mf-accent)" : "var(--mf-fg)",
          }}
        >
          {opt}
        </button>
      ))}
    </div>
  );
}

const HORIZON_OPTIONS: Record<string, keyof AssetDistRow> = {
  "1 Day": "avg_1d",
  "1 Week (7D)": "avg_7d",
  "1 Month (30D)": "avg_30d",
  "3 Months (90D)": "avg_90d",
  "1 Year (12M)": "avg_1y",
};

const ASSET_CLASS_SCOPES = ["All Asset Classes", "Equity", "Debt", "Hybrid", "Solution Oriented", "Other / Index / ETF"];

const CATEGORY_MATRIX_COLUMNS: ColumnConfig[] = [
  { key: "Asset Class", label: "Asset Class" },
  { key: "Category", label: "Category" },
  { key: "Schemes", label: "Schemes", format: "number", decimals: 0 },
  { key: "Avg TER %", label: "Avg TER %", format: "signed_pct" },
  { key: "Avg Exit %", label: "Avg Exit %", format: "signed_pct" },
  { key: "Avg 1D %", label: "Avg 1D %", format: "signed_pct" },
  { key: "Avg 7D %", label: "Avg 7D %", format: "signed_pct" },
  { key: "Avg 30D %", label: "Avg 30D %", format: "signed_pct" },
  { key: "Avg 90D %", label: "Avg 90D %", format: "signed_pct" },
  { key: "Avg 1Y %", label: "Avg 1Y %", format: "signed_pct" },
  { key: "52W High Gap %", label: "52W High Gap %", format: "signed_pct" },
];

const AMC_LEAGUE_COLUMNS: ColumnConfig[] = [
  { key: "fund_house", label: "Fund House" },
  { key: "schemes_count", label: "Schemes", format: "number", decimals: 0 },
  { key: "avg_30d", label: "Avg 30D %", format: "signed_pct" },
  { key: "avg_90d", label: "Avg 90D %", format: "signed_pct" },
  { key: "avg_1y", label: "Avg 1Y %", format: "signed_pct" },
];

export default function Home() {
  const { start, end } = useDateRangeStore();
  const [planScope, setPlanScope] = useState("All Plans");
  const [horizonChoice, setHorizonChoice] = useState("1 Month (30D)");
  const [amcView, setAmcView] = useState("Scheme Volume (Market Share)");
  const [catScope, setCatScope] = useState("All Asset Classes");
  const [catSearch, setCatSearch] = useState("");
  const debouncedCatSearch = useDebouncedValue(catSearch, 200);

  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ["overview-stats"],
    queryFn: getOverviewStats,
    refetchInterval: 60_000,
  });

  const { data: kpis } = useQuery({
    queryKey: ["overview-kpis", planScope, start, end],
    queryFn: () => getOverviewKpis(planScope, start || undefined, end || undefined),
    enabled: !!start && !!end,
  });

  const { data: trend } = useQuery({
    queryKey: ["macro-trend", start, end, planScope],
    queryFn: () => getMacroTrend(start, end, planScope),
    enabled: !!start && !!end,
  });

  const catParam = catScope === "All Asset Classes" ? "All" : catScope;
  const { data: catMatrix } = useQuery({
    queryKey: ["category-matrix", catParam],
    queryFn: () => getCategoryMatrix(catParam),
  });

  const span = start && end ? Math.round((new Date(end).getTime() - new Date(start).getTime()) / 86400000) : 0;

  const adv = kpis?.advancers ?? 0;
  const dec = kpis?.decliners ?? 0;
  const unch = kpis?.unchanged ?? 0;
  const totActive = adv + dec + unch;
  const advPct = totActive > 0 ? (adv / totActive) * 100 : 0;

  const catMatrixFiltered = useMemo(() => {
    if (!catMatrix || !debouncedCatSearch.trim()) return catMatrix ?? [];
    const tokens = debouncedCatSearch.toLowerCase().match(/[a-z0-9]+/g) ?? [];
    return catMatrix.filter((row) => {
      const hay = `${row["Asset Class"]} ${row["Category"]}`.toLowerCase();
      return tokens.every((t) => hay.includes(t));
    });
  }, [catMatrix, debouncedCatSearch]);

  const horizonKey = HORIZON_OPTIONS[horizonChoice];
  const assetBarFigure = useMemo(() => {
    const rows = stats?.asset_dist ?? [];
    const x = rows.map((r) => r.broad_category);
    const y = rows.map((r) => (r[horizonKey] as number) ?? 0);
    return {
      data: [
        {
          type: "bar",
          x,
          y,
          marker: { color: y.map((v) => (v >= 0 ? "#10B981" : "#EF4444")) },
          text: y.map((v) => `${v >= 0 ? "+" : ""}${v.toFixed(4)}%`),
          textposition: "outside",
          hovertemplate: "<b>%{x}</b>: %{y:+.4f}%<extra></extra>",
        },
      ],
      layout: {
        title: { text: `Average Return by Asset Class (${horizonChoice})` },
        showlegend: false,
        yaxis: { ticksuffix: "%", showgrid: true },
        xaxis: { showgrid: false },
        height: 360,
      },
    };
  }, [stats, horizonKey, horizonChoice]);

  const trendFigure = useMemo(() => {
    const rows = trend ?? [];
    const byClass = new Map<string, { x: string[]; y: number[] }>();
    for (const r of rows) {
      const cls = r["Asset Class"];
      if (!byClass.has(cls)) byClass.set(cls, { x: [], y: [] });
      byClass.get(cls)!.x.push(r.nav_date);
      byClass.get(cls)!.y.push(r["Indexed Performance"]);
    }
    const colors: Record<string, string> = { Equity: "#2563EB", Hybrid: "#F59E0B", Debt: "#10B981" };
    return {
      data: Array.from(byClass.entries()).map(([name, { x, y }]) => ({
        type: "scatter",
        mode: "lines",
        name,
        x,
        y,
        line: { color: colors[name] },
        hovertemplate: `${name}: <b>%{y:.2f}</b><extra></extra>`,
      })),
      layout: {
        height: 350,
        hovermode: "x unified",
        xaxis: { showgrid: true },
        legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "right", x: 1 },
      },
    };
  }, [trend]);

  const topAmcs = stats?.top_amcs ?? [];
  const amcFigure = useMemo(() => {
    if (amcView.startsWith("Scheme Volume")) {
      const top10 = topAmcs.slice(0, 10);
      return {
        data: [
          {
            type: "pie",
            labels: top10.map((r) => r.fund_house),
            values: top10.map((r) => r.schemes_count),
            hole: 0.45,
            textinfo: "label+percent",
            hovertemplate: "<b>%{label}</b><br>Schemes: %{value:,}<br>Share: %{percent}<extra></extra>",
          },
        ],
        layout: { title: { text: "Top 10 AMCs by Scheme Volume" }, showlegend: false, height: 360 },
      };
    }
    const withReturns = topAmcs
      .filter((r) => r.avg_30d !== null && r.avg_30d !== undefined)
      .sort((a, b) => (a.avg_30d ?? 0) - (b.avg_30d ?? 0))
      .slice(-10);
    return {
      data: [
        {
          type: "bar",
          orientation: "h",
          x: withReturns.map((r) => r.avg_30d),
          y: withReturns.map((r) => r.fund_house),
          marker: { color: withReturns.map((r) => ((r.avg_30d ?? 0) >= 0 ? "#10B981" : "#EF4444")) },
          text: withReturns.map((r) => `${(r.avg_30d ?? 0) >= 0 ? "+" : ""}${(r.avg_30d ?? 0).toFixed(2)}%`),
          textposition: "outside",
          hovertemplate: "<b>%{y}</b>: %{x:+.4f}%<extra></extra>",
        },
      ],
      layout: { title: { text: "Top Fund Houses by 30D Portfolio Return" }, showlegend: false, height: 360, xaxis: { title: { text: "Avg 30D Return (%)" } } },
    };
  }, [topAmcs, amcView]);

  return (
    <AppShell>
      <h1 className="mf-page-title">Indian Mutual Funds Analytics</h1>
      <p className="mf-page-caption">
        Institutional-grade macro analytics engine powered by official AMFI regulatory NAV records
      </p>

      <div className="mb-4">
        <PillRadio options={["All Plans", "Direct", "Regular"]} value={planScope} onChange={setPlanScope} />
      </div>

      {/* --- Executive KPI bar --- */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
        <StatCard
          title="Tracked Universe"
          value={statsLoading ? "..." : (stats?.total_schemes ?? 0).toLocaleString()}
          sub={statsLoading ? "" : `Across ${stats?.total_amcs ?? 0} Fund Houses`}
        />
        <StatCard
          title="NAV Data Points"
          value={statsLoading ? "..." : (stats?.total_nav_records ?? 0).toLocaleString()}
          sub={stats?.min_date ? `${stats.min_date} → ${stats.max_date}` : "All Available"}
        />
        <StatCard
          title={`Market Breadth (${span}D)`}
          value={`${advPct.toFixed(1)}% Positive`}
          sub={`Adv: ${adv.toLocaleString()} (${advPct.toFixed(1)}%) | Dec: ${dec.toLocaleString()}`}
          tone={toneOf(adv - dec)}
        />
        <StatCard
          title={`Median Alpha (${span}D)`}
          value={formatSignedPct(kpis?.median_return ?? null)}
          sub={`Industry Mean: ${formatSignedPct(kpis?.avg_return ?? null)}`}
          tone={toneOf(kpis?.median_return ?? null)}
        />
        <StatCard
          title={`Top Alpha (${span}D)`}
          value={kpis?.top_performer ? formatSignedPct(kpis.top_performer.return_pct) : "-"}
          sub={kpis?.top_performer?.name ?? "No data"}
          tone={kpis?.top_performer ? toneOf(kpis.top_performer.return_pct) : "neutral"}
        />
        <StatCard
          title={`Drawdown Laggard (${span}D)`}
          value={kpis?.lag_performer ? formatSignedPct(kpis.lag_performer.return_pct) : "-"}
          sub={kpis?.lag_performer?.name ?? "No data"}
          tone={kpis?.lag_performer ? toneOf(kpis.lag_performer.return_pct) : "neutral"}
          subTone="neg"
        />
      </div>

      {/* --- Section 1: Macro Asset Class Performance --- */}
      <div className="mt-8 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold">Macro Asset Class Performance</h2>
          <p className="mf-page-caption">Aggregated returns and scheme volume by SEBI broad asset classification (4-decimal precision).</p>
        </div>
        <PillRadio options={Object.keys(HORIZON_OPTIONS)} value={horizonChoice} onChange={setHorizonChoice} />
      </div>
      <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <PlotlyChart figure={assetBarFigure} />
        </div>
        <div className="lg:col-span-2">
          <DataTable
            keyField="broad_category"
            columns={[
              { key: "broad_category", label: "Asset Class" },
              { key: "count", label: "Schemes", format: "number", decimals: 0 },
              { key: "avg_1d", label: "Avg 1D %", format: "signed_pct" },
              { key: "avg_7d", label: "Avg 7D %", format: "signed_pct" },
              { key: "avg_30d", label: "Avg 30D %", format: "signed_pct" },
              { key: "avg_90d", label: "Avg 90D %", format: "signed_pct" },
              { key: "avg_1y", label: "Avg 1Y %", format: "signed_pct" },
            ]}
            rows={stats?.asset_dist ?? []}
          />
        </div>
      </div>

      {/* --- Section 2: Macro Asset Class Trajectory --- */}
      <div className="mt-8 border-t pt-6" style={{ borderColor: "var(--mf-border)" }}>
        <h2 className="text-lg font-bold">Macro Asset Class Trajectory (Indexed Performance, Base = 100)</h2>
        <p className="mf-page-caption">
          Historical relative trajectory of Equity, Hybrid, and Debt asset classes normalized to 100 at the start of the active window.
        </p>
        {trend && trend.length > 3 ? (
          <PlotlyChart figure={trendFigure} />
        ) : (
          <div className="mt-3">
            <Banner level="info">Insufficient historical points in this range to plot indexed trajectory.</Banner>
          </div>
        )}
      </div>

      {/* --- Section 3: Fund House (AMC) Intelligence --- */}
      <div className="mt-8 grid grid-cols-1 gap-4 border-t pt-6 lg:grid-cols-2" style={{ borderColor: "var(--mf-border)" }}>
        <div>
          <h2 className="text-lg font-bold">Top Fund Houses (AMCs)</h2>
          <div className="mt-2">
            <PillRadio
              options={["Scheme Volume (Market Share)", "Portfolio Performance (Avg 30D %)"]}
              value={amcView}
              onChange={setAmcView}
            />
          </div>
          <div className="mt-3">{topAmcs.length > 0 && <PlotlyChart figure={amcFigure} />}</div>
        </div>
        <div>
          <h2 className="text-lg font-bold">AMC League Table</h2>
          <p className="mf-page-caption">Leading fund houses ranked with scheme count and multi-horizon returns.</p>
          <div className="mt-3">
            <DataTable keyField="fund_house" columns={AMC_LEAGUE_COLUMNS} rows={topAmcs} />
          </div>
        </div>
      </div>

      {/* --- Section 4: Category Performance Matrix --- */}
      <div className="mt-8 border-t pt-6" style={{ borderColor: "var(--mf-border)" }}>
        <h2 className="text-lg font-bold">Detailed Category Performance Matrix</h2>
        <p className="mf-page-caption">
          Comprehensive risk & return metrics across standardized SEBI fund categories with 4-decimal regulatory precision.
        </p>
        <div className="filter-box mt-3 flex flex-wrap items-center gap-4">
          <PillRadio options={ASSET_CLASS_SCOPES} value={catScope} onChange={setCatScope} />
          <input
            type="text"
            placeholder="Filter table: small cap, liquid, flexi..."
            value={catSearch}
            onChange={(e) => setCatSearch(e.target.value)}
            className="rounded-lg border px-2 py-1.5 text-sm"
            style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
          />
        </div>
        <div className="mt-3">
          <DataTable keyField="Category" columns={CATEGORY_MATRIX_COLUMNS} rows={catMatrixFiltered} />
        </div>
      </div>
    </AppShell>
  );
}
