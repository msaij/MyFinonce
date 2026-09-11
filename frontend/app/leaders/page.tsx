"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { AppShell } from "@/components/layout/AppShell";
import { StatCard } from "@/components/shared/StatCard";
import { DataTable, ColumnConfig } from "@/components/shared/DataTable";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { Banner } from "@/components/shared/Banner";
import { formatSignedPct, toneOf } from "@/lib/format";
import { useDateRangeStore } from "@/lib/stores/dateRange";
import { useFilterStore } from "@/lib/stores/filters";
import { getMetaFilters } from "@/lib/api/meta";
import { getLeaders, type LeaderRow } from "@/lib/api/leaders";

const SECTION = "leaders";
const QUADRANT_COLORS: Record<string, string> = {
  "Institutional Alpha Stars (High Return, Low Risk)": "#10B981",
  "High-Beta Momentum (High Return, High Risk)": "#3B82F6",
  "Defensive Anchors (Low Return, Low Risk)": "#94A3B8",
  "Value Traps / Laggards (Low Return, High Risk)": "#EF4444",
};

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: string[] }) {
  return (
    <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border px-2 py-1.5 text-sm"
        style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
      >
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    </label>
  );
}

function QuadrantBox({ label, count, color, sub }: { label: string; count: number; color: string; sub: string }) {
  return (
    <div className="rounded-lg border-l-4 p-3" style={{ borderLeftColor: color, background: "var(--mf-card-bg)" }}>
      <b style={{ color }}>{label}</b>
      <div className="text-2xl font-bold">{count.toLocaleString()} funds</div>
      <small style={{ color: "var(--mf-muted)" }}>{sub}</small>
    </div>
  );
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}
function mean(values: number[]): number {
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
}

const ALPHA_TABLE_COLUMNS: ColumnConfig[] = [
  { key: "quartile_rank", label: "Quartile" },
  { key: "scheme_code", label: "AMFI Code", format: "number", decimals: 0 },
  { key: "display_name", label: "Scheme Name & Spec" },
  { key: "category", label: "Peer Category" },
  { key: "fund_house", label: "AMC" },
  { key: "expense_ratio", label: "TER %", format: "signed_pct" },
  { key: "period_return_pct", label: "Fund Return %", format: "signed_pct" },
  { key: "cat_median_return", label: "Category Median %", format: "signed_pct" },
  { key: "cat_alpha_pct", label: "Category Alpha %", format: "signed_pct" },
  { key: "annualized_vol_pct", label: "Ann. Volatility %", format: "signed_pct" },
];

const ABS_TABLE_COLUMNS: ColumnConfig[] = [
  { key: "quartile_rank", label: "Quartile" },
  { key: "scheme_code", label: "AMFI Code", format: "number", decimals: 0 },
  { key: "display_name", label: "Scheme Name" },
  { key: "fund_house", label: "AMC" },
  { key: "category", label: "Category" },
  { key: "expense_ratio", label: "TER %", format: "signed_pct" },
  { key: "period_return_pct", label: "Window Return %", format: "signed_pct" },
  { key: "cat_alpha_pct", label: "Category Alpha %", format: "signed_pct" },
  { key: "dist_from_52w_high_pct", label: "From 52W High %", format: "signed_pct" },
];

const LAGGARD_TABLE_COLUMNS: ColumnConfig[] = [
  { key: "diagnostic_classification", label: "Diagnostic Assessment" },
  { key: "quartile_rank", label: "Quartile" },
  { key: "scheme_code", label: "AMFI Code", format: "number", decimals: 0 },
  { key: "display_name", label: "Scheme Name & Spec" },
  { key: "category", label: "Category" },
  { key: "fund_house", label: "AMC" },
  { key: "period_return_pct", label: "Fund Return %", format: "signed_pct" },
  { key: "cat_median_return", label: "Category Median %", format: "signed_pct" },
  { key: "cat_alpha_pct", label: "Negative Alpha %", format: "signed_pct" },
  { key: "dist_from_52w_high_pct", label: "From 52W High %", format: "signed_pct" },
];

const ROTATION_TABLE_COLUMNS: ColumnConfig[] = [
  { key: "broad_category", label: "Asset Class" },
  { key: "category", label: "Category" },
  { key: "schemes_count", label: "Active Schemes", format: "number", decimals: 0 },
  { key: "avg_ter", label: "Avg TER %", format: "signed_pct" },
  { key: "median_return", label: "Median Return %", format: "signed_pct" },
  { key: "mean_return", label: "Average Return %", format: "signed_pct" },
  { key: "spread_pct", label: "Return Dispersion %", format: "signed_pct" },
  { key: "avg_vol", label: "Avg Volatility %", format: "signed_pct" },
];

const TABS = ["Category Alpha Leaders", "Risk-Reward 4-Quadrant Matrix", "Absolute Return Momentum", "Category Rotation", "Laggard Diagnostics"];

export default function LeadersPage() {
  const { start, end } = useDateRangeStore();
  const getFilter = useFilterStore((s) => s.getFilter);
  const setFilter = useFilterStore((s) => s.setFilter);

  const [broadCat, setBroadCatState] = useState(() => getFilter(SECTION, "broad_cat", "All Categories"));
  const [subCat, setSubCatState] = useState(() => getFilter(SECTION, "sub_cat", "All Sub-Categories"));
  const [planType, setPlanTypeState] = useState(() => getFilter(SECTION, "plan", "All Plans"));
  const [optionType, setOptionTypeState] = useState(() => getFilter(SECTION, "option", "All Options"));
  const [topN, setTopNState] = useState(() => getFilter(SECTION, "top_n", 10));
  const [search, setSearch] = useState("");
  const [activeTab, setActiveTab] = useState(TABS[0]);
  const [absSubTab, setAbsSubTab] = useState<"gainers" | "losers">("gainers");

  function setBroadCat(v: string) {
    setBroadCatState(v);
    setSubCatState("All Sub-Categories");
    setFilter(SECTION, "broad_cat", v);
    setFilter(SECTION, "sub_cat", "All Sub-Categories");
  }
  function setSubCat(v: string) {
    setSubCatState(v);
    setFilter(SECTION, "sub_cat", v);
  }
  function setPlanType(v: string) {
    setPlanTypeState(v);
    setFilter(SECTION, "plan", v);
  }
  function setOptionType(v: string) {
    setOptionTypeState(v);
    setFilter(SECTION, "option", v);
  }
  function setTopN(v: number) {
    setTopNState(v);
    setFilter(SECTION, "top_n", v);
  }
  function resetFilters() {
    setBroadCat("All Categories");
    setPlanType("All Plans");
    setOptionType("All Options");
    setTopN(10);
    setSearch("");
  }

  const { data: metaFilters } = useQuery({
    queryKey: ["meta-filters", broadCat],
    queryFn: () => getMetaFilters(broadCat === "All Categories" ? undefined : broadCat),
  });

  const { data, isLoading } = useQuery({
    queryKey: ["leaders", broadCat, subCat, planType, optionType, search, start, end],
    queryFn: () =>
      getLeaders({
        broad_cat: broadCat !== "All Categories" ? broadCat : undefined,
        sub_cat: subCat !== "All Sub-Categories" ? subCat : undefined,
        plan_type: planType !== "All Plans" ? planType : undefined,
        option_type: optionType !== "All Options" ? optionType : undefined,
        search: search || undefined,
        start: start || undefined,
        end: end || undefined,
      }),
    enabled: !!start && !!end,
  });

  const span = start && end ? Math.round((new Date(end).getTime() - new Date(start).getTime()) / 86400000) : 0;
  const rows = data?.rows ?? [];

  const alphaGainers = useMemo(() => [...rows].sort((a, b) => b.cat_alpha_pct - a.cat_alpha_pct).slice(0, topN), [rows, topN]);
  const alphaLosers = useMemo(() => [...rows].sort((a, b) => a.cat_alpha_pct - b.cat_alpha_pct).slice(0, topN), [rows, topN]);
  const absGainers = useMemo(() => [...rows].sort((a, b) => b.period_return_pct - a.period_return_pct).slice(0, topN), [rows, topN]);
  const absLosers = useMemo(() => [...rows].sort((a, b) => a.period_return_pct - b.period_return_pct).slice(0, topN), [rows, topN]);

  const quadCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const r of rows) if (r.quadrant) counts[r.quadrant] = (counts[r.quadrant] ?? 0) + 1;
    return counts;
  }, [rows]);
  const quadRows = useMemo(() => rows.filter((r) => r.quadrant), [rows]);

  const rotationRows = useMemo(() => {
    const groups = new Map<string, LeaderRow[]>();
    for (const r of rows) {
      const key = `${r.broad_category}||${r.category}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(r);
    }
    const out = Array.from(groups.entries()).map(([key, items]) => {
      const [broad_category, category] = key.split("||");
      const returns = items.map((r) => r.period_return_pct);
      const ters = items.map((r) => r.expense_ratio).filter((v): v is number => v !== null);
      const vols = items.map((r) => r.annualized_vol_pct).filter((v): v is number => v !== null);
      return {
        broad_category,
        category,
        schemes_count: items.length,
        avg_ter: ters.length ? Number(mean(ters).toFixed(4)) : null,
        median_return: Number(median(returns).toFixed(4)),
        mean_return: Number(mean(returns).toFixed(4)),
        spread_pct: Number((Math.max(...returns) - Math.min(...returns)).toFixed(4)),
        avg_vol: vols.length ? Number(mean(vols).toFixed(4)) : null,
      };
    });
    return out.sort((a, b) => b.median_return - a.median_return);
  }, [rows]);

  const laggardRows = useMemo(() => {
    return [...rows]
      .filter((r) => r.cat_alpha_pct < 0 || (r.dist_from_52w_high_pct ?? 0) < -10)
      .sort((a, b) => a.cat_alpha_pct - b.cat_alpha_pct)
      .slice(0, 100);
  }, [rows]);
  const laggardCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const r of laggardRows) counts[r.diagnostic_classification] = (counts[r.diagnostic_classification] ?? 0) + 1;
    return counts;
  }, [laggardRows]);

  function hbar(rowsForChart: LeaderRow[], valueKey: "cat_alpha_pct" | "period_return_pct", colorPos: string, colorNeg: string, title: string) {
    const sorted = [...rowsForChart].reverse(); // weakest-of-top-N first -> #1 ends up at top of the bar chart
    return {
      data: [
        {
          type: "bar",
          orientation: "h",
          x: sorted.map((r) => r[valueKey]),
          y: sorted.map((r) => r.display_name),
          marker: { color: sorted.map((r) => ((r[valueKey] as number) >= 0 ? colorPos : colorNeg)) },
          text: sorted.map((r) => `${(r[valueKey] as number) >= 0 ? "+" : ""}${(r[valueKey] as number).toFixed(4)}%`),
          textposition: "inside",
          hovertemplate: "<b>%{y}</b>: %{x:+.4f}%<extra></extra>",
        },
      ],
      layout: { title: { text: title }, xaxis: { ticksuffix: "%", showgrid: true }, height: 320 + sorted.length * 22 },
    };
  }

  const quadScatterFigure = useMemo(() => {
    const sample = quadRows.slice(0, 500);
    const byQuad = new Map<string, LeaderRow[]>();
    for (const r of sample) {
      if (!byQuad.has(r.quadrant!)) byQuad.set(r.quadrant!, []);
      byQuad.get(r.quadrant!)!.push(r);
    }
    return {
      data: Array.from(byQuad.entries()).map(([quad, items]) => ({
        type: "scatter",
        mode: "markers",
        name: quad,
        x: items.map((r) => r.annualized_vol_pct),
        y: items.map((r) => r.period_return_pct),
        text: items.map((r) => r.display_name),
        marker: { color: QUADRANT_COLORS[quad] ?? "#94A3B8", size: 7 },
        hovertemplate: "<b>%{text}</b><br>Vol: %{x:.2f}%<br>Return: %{y:.4f}%<extra></extra>",
      })),
      layout: {
        height: 540,
        xaxis: { title: { text: "Annualized Volatility % (Risk)" }, ticksuffix: "%", showgrid: true },
        yaxis: { title: { text: `Window Return % (${span}D)` }, ticksuffix: "%", showgrid: true },
        legend: { orientation: "h", yanchor: "bottom", y: -0.35, xanchor: "left", x: 0 },
        shapes:
          data?.med_vol !== null && data?.med_vol !== undefined && data?.med_ret !== null && data?.med_ret !== undefined
            ? [
                { type: "line", x0: data.med_vol, x1: data.med_vol, y0: 0, y1: 1, yref: "paper", line: { dash: "dash", color: "#94A3B8" } },
                { type: "line", x0: 0, x1: 1, xref: "paper", y0: data.med_ret, y1: data.med_ret, line: { dash: "dash", color: "#94A3B8" } },
              ]
            : [],
      },
    };
  }, [quadRows, data?.med_vol, data?.med_ret, span]);

  const rotationFigure = useMemo(() => {
    const top25 = rotationRows.slice(0, 25).reverse();
    return {
      data: [
        {
          type: "bar",
          orientation: "h",
          x: top25.map((r) => r.median_return),
          y: top25.map((r) => r.category),
          marker: { color: top25.map((r) => (r.median_return >= 0 ? "#10B981" : "#EF4444")) },
          text: top25.map((r) => `${r.median_return >= 0 ? "+" : ""}${r.median_return.toFixed(2)}%`),
          textposition: "inside",
          hovertemplate: "<b>%{y}</b><br>Median Return: %{x:+.4f}%<extra></extra>",
        },
      ],
      layout: { title: { text: "Top 25 Categories Ranked by Median Return" }, xaxis: { ticksuffix: "%", showgrid: true }, height: 620 },
    };
  }, [rotationRows]);

  return (
    <AppShell>
      <h1 className="mf-page-title">Leaders & Laggards — Relative Alpha & Momentum Engine</h1>
      <p className="mf-page-caption">
        Institutional peer outperformance (Alpha), risk-adjusted rankings, 4-quadrant momentum scatter, and category rotation.
      </p>

      {/* --- Filters --- */}
      <div className="filter-box mt-4">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm" style={{ color: "var(--mf-muted)" }}>
            Narrow down the mutual fund universe by asset class, category, plan, or search.
          </span>
          <button type="button" onClick={resetFilters} className="rounded-lg border px-3 py-1.5 text-xs font-semibold" style={{ borderColor: "var(--mf-border)" }}>
            Reset Filters
          </button>
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Select label="Asset Class" value={broadCat} onChange={setBroadCat} options={["All Categories", ...(metaFilters?.broad_categories ?? [])]} />
          <Select label="Category" value={subCat} onChange={setSubCat} options={["All Sub-Categories", ...(metaFilters?.sub_categories ?? [])]} />
          <Select label="Plan Type" value={planType} onChange={setPlanType} options={["All Plans", "Direct", "Regular"]} />
          <Select label="Option Type" value={optionType} onChange={setOptionType} options={["All Options", "Growth", "IDCW"]} />
        </div>
        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-[3fr_1fr]">
          <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
            Filter Schemes / Keyword
            <input
              type="text"
              placeholder="Type keywords in any order (e.g. motilal arbitrage, small cap, 118989)..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="rounded-lg border px-2 py-1.5 text-sm"
              style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
            />
          </label>
          <Select label="Show Leaders Count" value={String(topN)} onChange={(v) => setTopN(Number(v))} options={["5", "10", "15", "20", "25", "50"]} />
        </div>
      </div>

      {!start || !end ? null : isLoading ? (
        <p className="mt-6 text-sm" style={{ color: "var(--mf-muted)" }}>
          Loading...
        </p>
      ) : rows.length === 0 ? (
        <div className="mt-6">
          <Banner level="warning">No mutual fund data found matching your active filters and date window. Please adjust the filters above.</Banner>
        </div>
      ) : (
        <>
          {data && data.excluded_thin_data > 0 && (
            <p className="mt-3 text-xs" style={{ color: "var(--mf-muted)" }}>
              {data.excluded_thin_data.toLocaleString()} fund(s) with fewer than 5 trading days of history in this window were excluded from
              ranking.
            </p>
          )}

          {/* --- KPI bar --- */}
          <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCard
              title={`Market Breadth (${span}D)`}
              value={`${data!.advancers.toLocaleString()} / ${data!.total_funds.toLocaleString()}`}
              sub={`${data!.total_funds > 0 ? ((data!.advancers / data!.total_funds) * 100).toFixed(1) : "0.0"}% Advancing Funds`}
              tone={toneOf((data!.advancers / Math.max(data!.total_funds, 1)) * 100 - 50)}
            />
            <StatCard title="Universe Median Return" value={formatSignedPct(data!.market_median_return)} sub="Baseline Peer Benchmark" tone={toneOf(data!.market_median_return)} />
            <StatCard
              title="Highest Category Alpha"
              value={data!.top_alpha ? formatSignedPct(data!.top_alpha.return_pct) : "-"}
              sub={data!.top_alpha?.name ?? "No data"}
              tone={data!.top_alpha ? toneOf(data!.top_alpha.return_pct) : "neutral"}
            />
            <StatCard
              title="Top Momentum Category"
              value={data!.leading_category?.name ?? "N/A"}
              sub={`Median Return: ${formatSignedPct(data!.leading_category?.return_pct ?? null)}`}
              subTone={(data!.leading_category?.return_pct ?? 0) >= 0 ? "pos" : "neg"}
            />
          </div>

          {/* --- Tabs --- */}
          <div className="mt-6 flex flex-wrap gap-1.5 border-b pb-2" style={{ borderColor: "var(--mf-border)" }}>
            {TABS.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setActiveTab(t)}
                className="rounded-full px-3 py-1.5 text-xs font-semibold"
                style={{
                  background: t === activeTab ? "var(--mf-accent-bg)" : "transparent",
                  color: t === activeTab ? "var(--mf-accent)" : "var(--mf-fg)",
                }}
              >
                {t}
              </button>
            ))}
          </div>

          {activeTab === "Category Alpha Leaders" && (
            <div className="mt-4">
              <Banner level="info">
                Measures each fund&apos;s excess return against its exact peer Category Median -- neutralizes broad sectoral tides and highlights
                true fund-manager alpha.
              </Banner>
              <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
                <PlotlyChart figure={hbar(alphaGainers, "cat_alpha_pct", "#059669", "#059669", `Top ${topN} Category Alpha Leaders`)} />
                <PlotlyChart figure={hbar(alphaLosers, "cat_alpha_pct", "#FCA5A5", "#DC2626", `Top ${topN} Category Alpha Laggards`)} />
              </div>
              <h3 className="mt-4 text-base font-bold">Category Alpha Data Table</h3>
              <div className="mt-2">
                <DataTable columns={ALPHA_TABLE_COLUMNS} rows={alphaGainers} keyField="scheme_code" />
              </div>
            </div>
          )}

          {activeTab === "Risk-Reward 4-Quadrant Matrix" && (
            <div className="mt-4">
              <p className="mf-page-caption">
                Visualizes the trade-off between Risk (Annualized Volatility %) and Reward (Period Return %). Dashed crosshairs mark the active
                universe medians.
              </p>
              {quadRows.length === 0 ? (
                <Banner level="warning">
                  No fund in the current filtered universe has a computed volatility figure for this window. Widen the time horizon to populate
                  this chart.
                </Banner>
              ) : (
                <>
                  {data!.quadrant_excluded > 0 && (
                    <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
                      {data!.quadrant_excluded.toLocaleString()} fund(s) excluded from this chart -- no measurable volatility in this window.
                    </p>
                  )}
                  <div className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-4">
                    <QuadrantBox label="Institutional Alpha Stars" count={quadCounts["Institutional Alpha Stars (High Return, Low Risk)"] ?? 0} color="#10B981" sub="High Return, Low Volatility" />
                    <QuadrantBox label="High-Beta Momentum" count={quadCounts["High-Beta Momentum (High Return, High Risk)"] ?? 0} color="#3B82F6" sub="High Return, High Volatility" />
                    <QuadrantBox label="Defensive Anchors" count={quadCounts["Defensive Anchors (Low Return, Low Risk)"] ?? 0} color="#94A3B8" sub="Low Return, Low Volatility" />
                    <QuadrantBox label="Value Traps / Laggards" count={quadCounts["Value Traps / Laggards (Low Return, High Risk)"] ?? 0} color="#EF4444" sub="Low Return, High Volatility" />
                  </div>
                  <div className="mt-3">
                    <PlotlyChart figure={quadScatterFigure} />
                  </div>
                </>
              )}
            </div>
          )}

          {activeTab === "Absolute Return Momentum" && (
            <div className="mt-4">
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <PlotlyChart figure={hbar(absGainers, "period_return_pct", "#10B981", "#10B981", `Top ${topN} Absolute Gainers`)} />
                <PlotlyChart figure={hbar(absLosers, "period_return_pct", "#EF4444", "#EF4444", `Top ${topN} Absolute Losers`)} />
              </div>
              <div className="mt-4 flex gap-1.5">
                <button
                  type="button"
                  onClick={() => setAbsSubTab("gainers")}
                  className="rounded-full px-3 py-1.5 text-xs font-semibold"
                  style={{ background: absSubTab === "gainers" ? "var(--mf-accent-bg)" : "transparent", color: absSubTab === "gainers" ? "var(--mf-accent)" : "var(--mf-fg)" }}
                >
                  Top {topN} Gainers Table
                </button>
                <button
                  type="button"
                  onClick={() => setAbsSubTab("losers")}
                  className="rounded-full px-3 py-1.5 text-xs font-semibold"
                  style={{ background: absSubTab === "losers" ? "var(--mf-accent-bg)" : "transparent", color: absSubTab === "losers" ? "var(--mf-accent)" : "var(--mf-fg)" }}
                >
                  Top {topN} Losers Table
                </button>
              </div>
              <div className="mt-2">
                <DataTable columns={ABS_TABLE_COLUMNS} rows={absSubTab === "gainers" ? absGainers : absLosers} keyField="scheme_code" />
              </div>
            </div>
          )}

          {activeTab === "Category Rotation" && (
            <div className="mt-4">
              <p className="mf-page-caption">Identifies which asset classes and categories are leading or lagging the broader market in this timeframe.</p>
              <div className="mt-2">
                <PlotlyChart figure={rotationFigure} />
              </div>
              <h3 className="mt-4 text-base font-bold">Category Rotation Leaderboard Table</h3>
              <div className="mt-2">
                <DataTable columns={ROTATION_TABLE_COLUMNS} rows={rotationRows} keyField="category" />
              </div>
            </div>
          )}

          {activeTab === "Laggard Diagnostics" && (
            <div className="mt-4">
              <p className="mf-page-caption">
                Distinguishes between temporary Cyclical Laggards (entire sector depressed) and Structural Value Destroyers (fund severely
                lagging while category is healthy).
              </p>
              <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-3">
                <QuadrantBox label="Cyclical Dips" count={laggardCounts["Cyclical Dip (Category-wide correction; moving with peers)"] ?? 0} color="#EAB308" sub="Sector down, fund tracking peers" />
                <QuadrantBox label="Structural Value Destroyers" count={laggardCounts["Structural Drag (Chronic underperformance vs peers)"] ?? 0} color="#EF4444" sub="Severe alpha loss while peers gain" />
                <QuadrantBox label="Deep 52W Drawdowns" count={laggardCounts["Deep Drawdown (Far from 52W High)"] ?? 0} color="#F97316" sub=">15% below peak NAV" />
              </div>
              <h3 className="mt-4 text-base font-bold">Laggard Diagnostic Registry (Top 100 Underperforming Funds)</h3>
              <div className="mt-2">
                <DataTable columns={LAGGARD_TABLE_COLUMNS} rows={laggardRows} keyField="scheme_code" />
              </div>
            </div>
          )}
        </>
      )}
    </AppShell>
  );
}
