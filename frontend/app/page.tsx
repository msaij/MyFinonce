"use client";

import { useMemo, useState, Suspense } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";

import { AppShell } from "@/components/layout/AppShell";
import { StatCard } from "@/components/shared/StatCard";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { DataTable, ColumnConfig } from "@/components/shared/DataTable";
import { SearchCombobox } from "@/components/shared/SearchCombobox";
import { Banner } from "@/components/shared/Banner";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { formatSignedPct, formatDate, toneOf, CHART_MUTED_COLOR } from "@/lib/format";
import { useDebouncedValue, useUrlSync } from "@/lib/hooks";
import { useDateRangeStore } from "@/lib/stores/dateRange";
import { getMetaFilters } from "@/lib/api/meta";
import { getLeaders, type LeaderRow } from "@/lib/api/leaders";
import {
  type AssetDistRow,
  getCategoryMatrix,
  getMacroTrend,
  getOverviewKpis,
  getOverviewStats,
} from "@/lib/api/overview";
import { buildRotationFigure } from "@/lib/rotationChart";

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
          className="rounded-full border px-3 py-1 text-xs font-medium transition-colors"
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

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
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
          <option key={opt} value={opt} className="bg-white text-slate-900">
            {opt}
          </option>
        ))}
      </select>
    </label>
  );
}

function QuadrantBox({ label, count, color, sub }: { label: string; count: number; color: string; sub: string }) {
  return (
    <div className="rounded-lg border-l-4 p-3 shadow-sm" style={{ borderLeftColor: color, background: "var(--mf-card-bg)" }}>
      <b style={{ color }}>{label}</b>
      <div className="text-xl font-bold">{count.toLocaleString()} funds</div>
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

const QUADRANT_COLORS: Record<string, string> = {
  "Institutional Alpha Stars (High Return, Low Risk)": "#059669",
  "High-Beta Momentum (High Return, High Risk)": "#2563EB",
  "Defensive Anchors (Low Return, Low Risk)": CHART_MUTED_COLOR,
  "Value Traps / Laggards (Low Return, High Risk)": "#DC2626",
};

const MAIN_TABS = [
  { id: "pulse", label: "📊 Market Pulse & Macro Trends" },
  { id: "leaders", label: "🏆 Alpha Leaders & Laggards" },
  { id: "quadrant", label: "🎯 Risk-Reward 4-Quadrant" },
  { id: "rotation", label: "🔄 Category Rotation" },
] as const;

type MainTab = (typeof MAIN_TABS)[number]["id"];

export default function Home() {
  return (
    <Suspense fallback={<div className="p-6 text-sm">Loading Overview Dashboard...</div>}>
      <OverviewContent />
    </Suspense>
  );
}

function OverviewContent() {
  const searchParams = useSearchParams();
  const initialTab = (searchParams.get("tab") as MainTab) || "pulse";

  const { start, end, planType, optionType, setPlanType, setOptionType } = useDateRangeStore();
  const [activeMainTab, setActiveMainTab] = useState<MainTab>(
    MAIN_TABS.some((t) => t.id === initialTab) ? initialTab : "pulse"
  );

  // Overview page controls
  const [horizonChoice, setHorizonChoice] = useState(() => searchParams.get("horizon") || "1 Month (30D)");
  const [amcView, setAmcView] = useState("Scheme Volume (Market Share)");
  const [catScope, setCatScope] = useState(() => searchParams.get("catScope") || "All Asset Classes");
  const [catSearch, setCatSearch] = useState("");
  const debouncedCatSearch = useDebouncedValue(catSearch, 200);

  // Leaders & Laggards controls
  const [leadersBroadCat, setLeadersBroadCat] = useState(() => searchParams.get("broad_cat") || "All Categories");
  const [leadersSubCat, setLeadersSubCat] = useState(() => searchParams.get("sub_cat") || "All Sub-Categories");
  const [leadersTopN, setLeadersTopN] = useState(10);
  const [leadersSearch, setLeadersSearch] = useState("");
  const debouncedLeadersSearch = useDebouncedValue(leadersSearch, 400);
  const [leadersSubTab, setLeadersSubTab] = useState<"alpha" | "abs" | "laggard">(() => (searchParams.get("subTab") as "alpha" | "abs" | "laggard") || "alpha");
  const [absDirection, setAbsDirection] = useState<"gainers" | "losers">("gainers");
  const [volLookback, setVolLookback] = useState<string>(() => searchParams.get("vol_lookback") || "1Y");

  // Sync state with URL search parameters for institutional sharing and bookmarking
  useUrlSync({
    tab: activeMainTab !== "pulse" ? activeMainTab : undefined,
    horizon: horizonChoice !== "1 Month (30D)" ? horizonChoice : undefined,
    catScope: catScope !== "All Asset Classes" ? catScope : undefined,
    broad_cat: leadersBroadCat !== "All Categories" ? leadersBroadCat : undefined,
    sub_cat: leadersSubCat !== "All Sub-Categories" ? leadersSubCat : undefined,
    subTab: leadersSubTab !== "alpha" ? leadersSubTab : undefined,
    vol_lookback: volLookback !== "1Y" ? volLookback : undefined,
  });

  // Global meta & overview data
  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ["overview-stats"],
    queryFn: getOverviewStats,
    refetchInterval: 60_000,
  });

  const { data: kpis } = useQuery({
    queryKey: ["overview-kpis", planType, optionType, start, end],
    queryFn: () => getOverviewKpis(planType, optionType, start || undefined, end || undefined),
    placeholderData: keepPreviousData,
  });

  const { data: trend } = useQuery({
    queryKey: ["macro-trend", start, end, planType, optionType],
    queryFn: () => getMacroTrend(start, end, planType, optionType),
    enabled: !!start && !!end,
    placeholderData: keepPreviousData,
  });

  const catParam = catScope === "All Asset Classes" ? "All" : catScope;
  const { data: catMatrix } = useQuery({
    queryKey: ["category-matrix", catParam],
    queryFn: () => getCategoryMatrix(catParam),
  });

  const { data: metaFilters } = useQuery({
    queryKey: ["meta-filters", leadersBroadCat],
    queryFn: () => getMetaFilters(leadersBroadCat === "All Categories" ? undefined : leadersBroadCat),
  });

  // Leaders & Laggards dataset
  const { data: leadersData, isLoading: leadersLoading } = useQuery({
    queryKey: ["leaders", leadersBroadCat, leadersSubCat, planType, optionType, debouncedLeadersSearch, start, end, volLookback],
    queryFn: () =>
      getLeaders({
        broad_cat: leadersBroadCat !== "All Categories" ? leadersBroadCat : undefined,
        sub_cat: leadersSubCat !== "All Sub-Categories" ? leadersSubCat : undefined,
        plan_type: planType !== "All Plans" ? planType : undefined,
        option_type: optionType !== "All Options" ? optionType : undefined,
        search: debouncedLeadersSearch || undefined,
        start: start || undefined,
        end: end || undefined,
        vol_lookback: volLookback,
      }),
    placeholderData: keepPreviousData,
  });

  const span = start && end ? Math.round((new Date(end).getTime() - new Date(start).getTime()) / 86400000) : 0;

  const adv = kpis?.advancers ?? 0;
  const dec = kpis?.decliners ?? 0;
  const unch = kpis?.unchanged ?? 0;
  const totActive = adv + dec + unch;
  const advPct = totActive > 0 ? (adv / totActive) * 100 : 0;

  // Filtered Category Matrix (multi-token search)
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
        hoversort: "value descending",
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
            textinfo: "percent",
            hovertemplate: "<b>%{label}</b><br>Schemes: %{value:,}<br>Share: %{percent}<extra></extra>",
          },
        ],
        layout: {
          title: { text: "Top 10 AMCs by Scheme Volume" },
          showlegend: true,
          legend: { orientation: "h", x: 0.5, xanchor: "center", y: -0.05, yanchor: "top" },
          height: 480,
          margin: { t: 40, r: 10, b: 10, l: 10 },
        },
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

  // Leaders calculations
  const leaderRows = leadersData?.rows ?? [];
  const alphaGainers = useMemo(
    () => [...leaderRows].sort((a, b) => b.cat_alpha_pct - a.cat_alpha_pct).slice(0, leadersTopN),
    [leaderRows, leadersTopN]
  );
  const alphaLosers = useMemo(
    () => [...leaderRows].sort((a, b) => a.cat_alpha_pct - b.cat_alpha_pct).slice(0, leadersTopN),
    [leaderRows, leadersTopN]
  );
  const absGainers = useMemo(
    () => [...leaderRows].sort((a, b) => b.period_return_pct - a.period_return_pct).slice(0, leadersTopN),
    [leaderRows, leadersTopN]
  );
  const absLosers = useMemo(
    () => [...leaderRows].sort((a, b) => a.period_return_pct - b.period_return_pct).slice(0, leadersTopN),
    [leaderRows, leadersTopN]
  );

  const quadCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const r of leaderRows) if (r.quadrant) counts[r.quadrant] = (counts[r.quadrant] ?? 0) + 1;
    return counts;
  }, [leaderRows]);
  const quadRows = useMemo(() => leaderRows.filter((r) => r.quadrant), [leaderRows]);

  const rotationRows = useMemo(() => {
    const groups = new Map<string, LeaderRow[]>();
    for (const r of leaderRows) {
      const key = `${r.broad_category}||${r.category}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(r);
    }
    const out = Array.from(groups.entries()).map(([key, items]) => {
      const [broad_category, category] = key.split("||");
      const returns = items.map((r) => r.period_return_pct);
      const ters = items.map((r) => r.expense_ratio).filter((v): v is number => v !== null);
      const vols = items.map((r) => r.annualized_vol_pct).filter((v): v is number => v !== null);
      const spread = returns.length > 0 ? Math.max(...returns) - Math.min(...returns) : 0;
      return {
        broad_category,
        category,
        schemes_count: items.length,
        avg_ter: ters.length ? Number(mean(ters).toFixed(4)) : null,
        median_return: Number(median(returns).toFixed(4)),
        mean_return: Number(mean(returns).toFixed(4)),
        spread_pct: Number(spread.toFixed(4)),
        avg_vol: vols.length ? Number(mean(vols).toFixed(4)) : null,
      };
    });
    return out.sort((a, b) => b.median_return - a.median_return);
  }, [leaderRows]);

  const laggardRows = useMemo(() => {
    return [...leaderRows]
      .filter((r) => r.cat_alpha_pct < 0 || (r.dist_from_52w_high_pct ?? 0) < -10)
      .sort((a, b) => a.cat_alpha_pct - b.cat_alpha_pct)
      .slice(0, 100);
  }, [leaderRows]);

  const laggardCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const r of laggardRows) counts[r.diagnostic_classification] = (counts[r.diagnostic_classification] ?? 0) + 1;
    return counts;
  }, [laggardRows]);

  function hbar(rowsForChart: LeaderRow[], valueKey: "cat_alpha_pct" | "period_return_pct", colorPos: string, colorNeg: string, title: string) {
    const sorted = [...rowsForChart].reverse();
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
      layout: { title: { text: title }, xaxis: { ticksuffix: "%", showgrid: true }, height: Math.max(320, 180 + sorted.length * 24) },
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
        marker: { color: QUADRANT_COLORS[quad] ?? CHART_MUTED_COLOR, size: 7 },
        hovertemplate: "<b>%{text}</b><br>Vol: %{x:.2f}%<br>Return: %{y:.4f}%<extra></extra>",
      })),
      layout: {
        height: 520,
        xaxis: { title: { text: "Annualized Volatility % (Risk)" }, ticksuffix: "%", showgrid: true },
        yaxis: { title: { text: `Window Return % (${span}D)` }, ticksuffix: "%", showgrid: true },
        legend: { orientation: "h", yanchor: "bottom", y: -0.35, xanchor: "left", x: 0 },
        shapes:
          leadersData?.med_vol !== null && leadersData?.med_vol !== undefined && leadersData?.med_ret !== null && leadersData?.med_ret !== undefined
            ? [
                { type: "line", x0: leadersData.med_vol, x1: leadersData.med_vol, y0: 0, y1: 1, yref: "paper", line: { dash: "dash", color: CHART_MUTED_COLOR } },
                { type: "line", x0: 0, x1: 1, xref: "paper", y0: leadersData.med_ret, y1: leadersData.med_ret, line: { dash: "dash", color: CHART_MUTED_COLOR } },
              ]
            : [],
      },
    };
  }, [quadRows, leadersData?.med_vol, leadersData?.med_ret, span]);

  const rotationFigure = useMemo(() => {
    return buildRotationFigure(rotationRows);
  }, [rotationRows]);

  return (
    <AppShell>
      {/* --- Executive Header --- */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="mf-page-title">Executive Market Overview & Alpha Intelligence</h1>
          <p className="mf-page-caption">
            Comprehensive market breadth, macro asset class trends, relative alpha rankings, and institutional quadrant analysis.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>Active Filters:</span>
          <span className="mf-pill mf-pill-neutral font-medium">Plan: {planType}</span>
          <span className="mf-pill mf-pill-neutral font-medium">Option: {optionType}</span>
        </div>
      </div>

      {/* --- Executive KPI Bar --- */}
      <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
        <StatCard
          title="Tracked Universe"
          value={statsLoading ? "..." : (stats?.total_schemes ?? 0).toLocaleString()}
          sub={statsLoading ? "" : `Across ${stats?.total_amcs ?? 0} Fund Houses`}
        />
        <StatCard
          title="NAV History Points"
          value={statsLoading ? "..." : (stats?.total_nav_records ?? 0).toLocaleString()}
          sub={stats?.min_date ? `${formatDate(stats.min_date)} → ${formatDate(stats.max_date)}` : "All Available"}
        />
        <StatCard
          title={`Market Breadth (${span}D)`}
          value={`${advPct.toFixed(1)}%`}
          sub={`Adv: ${adv.toLocaleString()} | Dec: ${dec.toLocaleString()}`}
          tone={toneOf(adv - dec)}
        />
        <StatCard
          title={`Median Return (${span}D)`}
          value={formatSignedPct(kpis?.median_return ?? null)}
          sub={`Industry Mean: ${formatSignedPct(kpis?.avg_return ?? null)}`}
          tone={toneOf(kpis?.median_return ?? null)}
        />
        <StatCard
          title={`Top Alpha Outperformer`}
          value={leadersData?.top_alpha ? formatSignedPct(leadersData.top_alpha.return_pct) : kpis?.top_performer ? formatSignedPct(kpis.top_performer.return_pct) : "-"}
          sub={leadersData?.top_alpha?.name ?? kpis?.top_performer?.name ?? "No data"}
          tone={leadersData?.top_alpha ? toneOf(leadersData.top_alpha.return_pct) : "neutral"}
        />
        <StatCard
          title={`Leading Category (${span}D)`}
          value={leadersData?.leading_category?.name ?? "N/A"}
          sub={`Median: ${formatSignedPct(leadersData?.leading_category?.return_pct ?? null)}`}
          subTone={(leadersData?.leading_category?.return_pct ?? 0) >= 0 ? "pos" : "neg"}
        />
      </div>

      {/* --- Main Dashboard Navigation Tabs --- */}
      <div className="mt-6 flex flex-wrap gap-2 border-b pb-2" style={{ borderColor: "var(--mf-border)" }}>
        {MAIN_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveMainTab(tab.id)}
            className="rounded-full px-4 py-2 text-sm font-semibold transition-all"
            style={{
              background: activeMainTab === tab.id ? "var(--mf-accent)" : "var(--mf-card-bg)",
              color: activeMainTab === tab.id ? "white" : "var(--mf-fg)",
              border: "1px solid var(--mf-border)",
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ========================================================================= */}
      {/* TAB 1: MARKET PULSE & MACRO TRENDS                                        */}
      {/* ========================================================================= */}
      {activeMainTab === "pulse" && (
        <div className="mt-6 space-y-8">
          {/* Macro Asset Class Performance */}
          <div className="rounded-xl border p-5" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-bold">Macro Asset Class Performance</h2>
                <p className="mf-page-caption">Aggregated returns and scheme volume by SEBI broad asset classification.</p>
              </div>
              <PillRadio options={Object.keys(HORIZON_OPTIONS)} value={horizonChoice} onChange={setHorizonChoice} />
            </div>
            <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-5">
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
          </div>

          {/* Macro Asset Class Trajectory */}
          <div className="rounded-xl border p-5" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
            <h2 className="text-lg font-bold">Macro Asset Class Trajectory (Indexed Performance, Base = 100)</h2>
            <p className="mf-page-caption">
              Historical relative trajectory of Equity, Hybrid, and Debt asset classes normalized to 100 at the start of the active window.
            </p>
            <div className="mt-3">
              {trend && trend.length > 3 ? (
                <PlotlyChart figure={trendFigure} />
              ) : (
                <Banner level="info">Insufficient historical points in this range to plot indexed trajectory.</Banner>
              )}
            </div>
          </div>

          {/* AMC Intelligence & League Table */}
          <div className="rounded-xl border p-5" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
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
          </div>

          {/* Category Performance Matrix */}
          <div className="rounded-xl border p-5" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
            <h2 className="text-lg font-bold">Category Performance Matrix</h2>
            <p className="mf-page-caption">
              Comprehensive risk & return metrics across standardized SEBI fund categories with 4-decimal regulatory precision.
            </p>
            <div className="filter-box mt-3 flex flex-wrap items-center gap-4">
              <PillRadio options={ASSET_CLASS_SCOPES} value={catScope} onChange={setCatScope} />
              <input
                type="text"
                placeholder="Filter categories in any order (e.g. small cap, liquid)..."
                value={catSearch}
                onChange={(e) => setCatSearch(e.target.value)}
                className="flex-1 min-w-[260px] rounded-lg border px-3 py-1.5 text-sm"
                style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
              />
            </div>
            <div className="mt-3">
              <DataTable keyField="Category" columns={CATEGORY_MATRIX_COLUMNS} rows={catMatrixFiltered} />
            </div>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* TAB 2: ALPHA LEADERS & LAGGARDS                                           */}
      {/* ========================================================================= */}
      {activeMainTab === "leaders" && (
        <div className="mt-6 space-y-6">
          {/* Filter Toolbar */}
          <div className="filter-box">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-4 lg:grid-cols-6">
              <Select
                label="Asset Class"
                value={leadersBroadCat}
                onChange={(v) => {
                  setLeadersBroadCat(v);
                  setLeadersSubCat("All Sub-Categories");
                }}
                options={["All Categories", ...(metaFilters?.broad_categories ?? [])]}
              />
              <Select
                label="Peer Sub-Category"
                value={leadersSubCat}
                onChange={setLeadersSubCat}
                options={["All Sub-Categories", ...(metaFilters?.sub_categories ?? [])]}
              />
              <Select
                label="Plan Type (Global)"
                value={planType}
                onChange={(v) => setPlanType(v as any)}
                options={["All Plans", "Direct", "Regular"]}
              />
              <Select
                label="Option Type (Global)"
                value={optionType}
                onChange={(v) => setOptionType(v as any)}
                options={["All Options", "Growth", "IDCW"]}
              />
              <Select
                label="Show Top N"
                value={String(leadersTopN)}
                onChange={(v) => setLeadersTopN(Number(v))}
                options={["5", "10", "15", "20", "25", "50"]}
              />
              <div className="flex flex-col gap-1">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium" style={{ color: "var(--mf-muted)" }}>Vol. Lookback</span>
                  <FormulaTooltip
                    label="Volatility Lookback Window"
                    formula="\sigma_{\text{ann}} = \sigma_{\text{daily}} \times \sqrt{252}"
                    description="Annualized volatility lookback window for ranking, quadrant classification, and return-to-risk scoring. 1-Year (default) measures current regime risk; Full Window computes volatility across the entire active date range."
                  />
                </div>
                <select
                  value={volLookback}
                  onChange={(e) => setVolLookback(e.target.value)}
                  className="rounded-lg border px-2 py-1.5 text-sm"
                  style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
                >
                  {["1Y", "6M", "3Y", "Full Window"].map((opt) => (
                    <option key={opt} value={opt} className="bg-white text-slate-900">
                      {opt}
                    </option>
                  ))}
                </select>
              </div>
              <div className="col-span-1 sm:col-span-2 md:col-span-2 lg:col-span-2">
                <div className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
                  <span>Search Fund Name (Any word order, case-insensitive dropdown)</span>
                  <SearchCombobox
                    placeholder="Search fund name or AMFI code..."
                    value={leadersSearch}
                    onChangeQuery={setLeadersSearch}
                    onClear={() => setLeadersSearch("")}
                    extraParams={{
                      plan_type: planType !== "All Plans" ? planType : undefined,
                      option_type: optionType !== "All Options" ? optionType : undefined,
                      broad_cat: leadersBroadCat !== "All Categories" ? leadersBroadCat : undefined,
                      sub_cat: leadersSubCat !== "All Sub-Categories" ? leadersSubCat : undefined,
                    }}
                    onSelect={(scheme) => setLeadersSearch(scheme.scheme_name)}
                  />
                </div>
              </div>
            </div>
          </div>

          {/* Sub-tabs for Leaders */}
          <div className="flex flex-wrap gap-2 border-b pb-2" style={{ borderColor: "var(--mf-border)" }}>
            <button
              type="button"
              onClick={() => setLeadersSubTab("alpha")}
              className="rounded-full px-3 py-1 text-xs font-semibold"
              style={{
                background: leadersSubTab === "alpha" ? "var(--mf-accent-bg)" : "transparent",
                color: leadersSubTab === "alpha" ? "var(--mf-accent)" : "var(--mf-fg)",
              }}
            >
              Category Alpha Leaders & Laggards
            </button>
            <button
              type="button"
              onClick={() => setLeadersSubTab("abs")}
              className="rounded-full px-3 py-1 text-xs font-semibold"
              style={{
                background: leadersSubTab === "abs" ? "var(--mf-accent-bg)" : "transparent",
                color: leadersSubTab === "abs" ? "var(--mf-accent)" : "var(--mf-fg)",
              }}
            >
              Absolute Return Momentum
            </button>
            <button
              type="button"
              onClick={() => setLeadersSubTab("laggard")}
              className="rounded-full px-3 py-1 text-xs font-semibold"
              style={{
                background: leadersSubTab === "laggard" ? "var(--mf-accent-bg)" : "transparent",
                color: leadersSubTab === "laggard" ? "var(--mf-accent)" : "var(--mf-fg)",
              }}
            >
              Laggard Diagnostics
            </button>
          </div>

          <div className="min-h-[600px]">
            {leadersLoading && !leadersData ? (
              <p className="text-sm" style={{ color: "var(--mf-muted)" }}>
                Analyzing leaders & laggards metrics across {span} days...
              </p>
            ) : leaderRows.length === 0 ? (
              <Banner level="warning">
                No mutual fund data found matching your active filters. Please adjust the filters or run a data sync.
              </Banner>
            ) : (
              <>
                {leadersSubTab === "alpha" && (
                  <div className="space-y-6">
                    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                      <div className="rounded-xl border p-4" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
                        <PlotlyChart figure={hbar(alphaGainers, "cat_alpha_pct", "#10B981", "#EF4444", `Top ${leadersTopN} Category Alpha Stars (Outperformance %)`)} />
                      </div>
                      <div className="rounded-xl border p-4" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
                        <PlotlyChart figure={hbar(alphaLosers, "cat_alpha_pct", "#10B981", "#EF4444", `Top ${leadersTopN} Peer Alpha Laggards (Underperformance %)`)} />
                      </div>
                    </div>
                    <div>
                      <h3 className="text-base font-bold">Peer Alpha Leaderboard (Quartile Ranked)</h3>
                      <div className="mt-2">
                        <DataTable columns={ALPHA_TABLE_COLUMNS} rows={alphaGainers} keyField="scheme_code" />
                      </div>
                    </div>
                  </div>
                )}

                {leadersSubTab === "abs" && (
                  <div className="space-y-6">
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => setAbsDirection("gainers")}
                        className="rounded-lg px-3 py-1.5 text-xs font-semibold"
                        style={{
                          background: absDirection === "gainers" ? "var(--mf-accent)" : "var(--mf-card-bg)",
                          color: absDirection === "gainers" ? "white" : "var(--mf-fg)",
                          border: "1px solid var(--mf-border)",
                        }}
                      >
                        Top Gainers (Absolute Return)
                      </button>
                      <button
                        type="button"
                        onClick={() => setAbsDirection("losers")}
                        className="rounded-lg px-3 py-1.5 text-xs font-semibold"
                        style={{
                          background: absDirection === "losers" ? "var(--mf-accent)" : "var(--mf-card-bg)",
                          color: absDirection === "losers" ? "white" : "var(--mf-fg)",
                          border: "1px solid var(--mf-border)",
                        }}
                      >
                        Top Losers (Deepest Drawdown)
                      </button>
                    </div>
                    <div className="rounded-xl border p-4" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
                      <PlotlyChart
                        figure={hbar(
                          absDirection === "gainers" ? absGainers : absLosers,
                          "period_return_pct",
                          "#10B981",
                          "#EF4444",
                          `${absDirection === "gainers" ? "Top" : "Bottom"} ${leadersTopN} Absolute Window Return (%)`
                        )}
                      />
                    </div>
                    <div className="mt-2">
                      <DataTable
                        columns={ABS_TABLE_COLUMNS}
                        rows={absDirection === "gainers" ? absGainers : absLosers}
                        keyField="scheme_code"
                      />
                    </div>
                  </div>
                )}

                {leadersSubTab === "laggard" && (
                  <div className="space-y-6">
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                      {Object.entries(laggardCounts).map(([diag, cnt]) => (
                        <div key={diag} className="rounded-lg border p-3" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
                          <div className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>{diag}</div>
                          <div className="text-xl font-bold mt-1">{cnt} funds</div>
                        </div>
                      ))}
                    </div>
                    <DataTable columns={LAGGARD_TABLE_COLUMNS} rows={laggardRows} keyField="scheme_code" />
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* TAB 3: RISK-REWARD 4-QUADRANT MATRIX                                      */}
      {/* ========================================================================= */}
      {activeMainTab === "quadrant" && (
        <div className="mt-6 space-y-6">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <QuadrantBox
              label="Institutional Alpha Stars"
              count={quadCounts["Institutional Alpha Stars (High Return, Low Risk)"] ?? 0}
              color="#059669"
              sub="High Return, Low Volatility"
            />
            <QuadrantBox
              label="High-Beta Momentum"
              count={quadCounts["High-Beta Momentum (High Return, High Risk)"] ?? 0}
              color="#2563EB"
              sub="High Return, High Volatility"
            />
            <QuadrantBox
              label="Defensive Anchors"
              count={quadCounts["Defensive Anchors (Low Return, Low Risk)"] ?? 0}
              color={CHART_MUTED_COLOR}
              sub="Low Return, Low Volatility"
            />
            <QuadrantBox
              label="Value Traps / Laggards"
              count={quadCounts["Value Traps / Laggards (Low Return, High Risk)"] ?? 0}
              color="#DC2626"
              sub="Low Return, High Volatility"
            />
          </div>

          <div className="rounded-xl border p-4" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
            <h3 className="text-base font-bold mb-1">Risk vs. Return Distribution (4-Quadrant Scatter)</h3>
            <p className="mf-page-caption mb-3">
              Dashed lines represent median universe return ({leadersData?.med_ret !== null ? `${leadersData?.med_ret?.toFixed(2)}%` : "-"}) and annualized volatility ({leadersData?.med_vol !== null ? `${leadersData?.med_vol?.toFixed(2)}%` : "-"}).
              Quadrant labels including &quot;Institutional Alpha Stars&quot; and &quot;Value Traps / Laggards&quot; are descriptive versus that median, not a recommendation.
            </p>
            {quadRows.length > 0 ? (
              <PlotlyChart figure={quadScatterFigure} />
            ) : (
              <Banner level="info">Insufficient volatility data in the current date window to plot the 4-quadrant matrix.</Banner>
            )}
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* TAB 4: CATEGORY ROTATION                                                  */}
      {/* ========================================================================= */}
      {activeMainTab === "rotation" && (
        <div className="mt-6 space-y-6">
          <div className="rounded-xl border p-4" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
            <PlotlyChart figure={rotationFigure} />
          </div>
          <div className="rounded-xl border p-4" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
            <h3 className="text-base font-bold mb-2">Category Dispersion & Risk-Adjusted Leadership</h3>
            <DataTable columns={ROTATION_TABLE_COLUMNS} rows={rotationRows} keyField="category" />
          </div>
        </div>
      )}
    </AppShell>
  );
}
