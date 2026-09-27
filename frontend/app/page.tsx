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
import { AllFundsTab } from "@/components/overview/AllFundsTab";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { formatSignedPct, formatDate, toneOf, CHART_MUTED_COLOR } from "@/lib/format";
import { useDebouncedValue, useUrlSync } from "@/lib/hooks";
import { useDateRangeStore } from "@/lib/stores/dateRange";
import { getLeaders, getRotation, type LeaderRow, type RotationCategory, type RotationQuadrant } from "@/lib/api/leaders";
import {
  type AmcScoreRow,
  type AssetDistRow,
  type CategoryMatrixRow,
  getCategoryMatrix,
  getMacroTrend,
  getOverviewStats,
  getPulseKpis,
} from "@/lib/api/overview";
import { formatTer } from "@/lib/holdings";
import {
  ASSET_CLASS_COLORS,
  ambiguousCategories,
  formatCrore,
  formatPp,
  onePerFund,
  peerGroupLabel,
  periodLabel,
  robustRange,
  windowDays,
} from "@/lib/overview";

function PillRadio({ options, value, onChange }: { options: string[]; value: string; onChange: (v: string) => void }) {
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

type Opt = string | { value: string; label: string };

function Select({ label, value, onChange, options, tip }: { label: string; value: string; onChange: (v: string) => void; options: Opt[]; tip?: string }) {
  return (
    <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
      <span className="flex items-center">
        {label}
        {tip && <FormulaTooltip label={label} description={tip} />}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border px-2 py-1.5 text-sm"
        style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
      >
        {options.map((opt) => {
          const o = typeof opt === "string" ? { value: opt, label: opt } : opt;
          return (
            <option key={o.value} value={o.value} className="bg-white text-slate-900">
              {o.label}
            </option>
          );
        })}
      </select>
    </label>
  );
}

function Card({ title, about, caption, right, children }: { title: string; about: string; caption?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border p-5" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center text-lg font-bold">
            {title}
            <FormulaTooltip label={title} description={about} />
          </h2>
          {caption && <p className="mf-page-caption">{caption}</p>}
        </div>
        {right}
      </div>
      <div className="mt-3">{children}</div>
    </div>
  );
}

/** A clickable quadrant tile: picks which quadrant's funds (or categories) the list below shows. */
function QuadrantTile({ label, count, share, color, about, active, onClick, sub }: {
  label: string; count: number; share: number | null; color: string; about: string; active: boolean; onClick: () => void; sub?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="rounded-lg border border-l-4 p-3 text-left shadow-sm transition-colors"
      style={{ borderLeftColor: color, borderColor: active ? color : "var(--mf-border)", background: active ? "var(--mf-accent-bg)" : "var(--mf-card-bg)" }}
    >
      <span className="flex items-center">
        <b style={{ color }}>{label}</b>
        <FormulaTooltip label={label} description={about} />
      </span>
      <span className="block text-xl font-bold">
        {count.toLocaleString()}
        {share !== null && <span className="ml-1 text-sm font-medium" style={{ color: "var(--mf-muted)" }}>({share.toFixed(0)}%)</span>}
      </span>
      {sub && <small className="block truncate" style={{ color: "var(--mf-muted)" }} title={sub}>{sub}</small>}
    </button>
  );
}

const signed2 = (v: unknown) => formatSignedPct(v as number | null, 2);
const toned = (v: number | null | undefined, text: string) => (
  <span className={toneOf(v ?? null) === "pos" ? "mf-pos" : toneOf(v ?? null) === "neg" ? "mf-neg" : ""}>{text}</span>
);

const HORIZON_OPTIONS: Record<string, "1d" | "7d" | "30d" | "90d" | "1y"> = {
  "1 Day": "1d",
  "1 Week (7D)": "7d",
  "1 Month (30D)": "30d",
  "3 Months (90D)": "90d",
  "1 Year (12M)": "1y",
};

const ASSET_TABLE_COLUMNS: ColumnConfig[] = [
  { key: "asset_class", label: "Asset class", tooltip: "Where the money actually is, from each scheme's SEBI category and name. Index funds and ETFs go by what they track, gold and silver funds are Gold & Commodities, overseas funds are International Equity, and arbitrage funds are Cash & Liquid (they are hedged)." },
  { key: "count", label: "Funds", format: "number", decimals: 0, tooltip: "Growth-type plans in the class, whose returns are the medians shown. IDCW plans are left out: every payout drops their NAV, which would read as a loss." },
  { key: "aum_cr", label: "AUM", render: (_r, v) => formatCrore(v as number | null), tooltip: "Assets under management as AMFI reports them, summed per fund (all plans together)." },
  { key: "aum_share_pct", label: "Share of AUM", format: "pct", decimals: 1, tooltip: "This class's share of the whole industry's assets." },
  { key: "med_1d", label: "1D", render: (_r, v) => signed2(v), tooltip: "Median fund's trailing return over each window, as of the latest NAV date." },
  { key: "med_7d", label: "7D", render: (_r, v) => signed2(v) },
  { key: "med_30d", label: "30D", render: (_r, v) => signed2(v) },
  { key: "med_90d", label: "90D", render: (_r, v) => signed2(v) },
  { key: "med_1y", label: "1Y", render: (_r, v) => signed2(v), tooltip: "Median over funds at least a year old (see the hover for how many)." },
];

const SCORECARD_COLUMNS: ColumnConfig[] = [
  { key: "fund_house", label: "Fund house" },
  { key: "aum_cr", label: "AUM", render: (_r, v) => formatCrore(v as number | null), tooltip: "The house's total assets as AMFI reports them." },
  { key: "ranked_schemes", label: "Ranked schemes", format: "number", decimals: 0, tooltip: "Growth-type schemes at least a year old with 5 or more peers (same asset class, SEBI category and plan). A house needs 5 of them to be scored." },
  { key: "median_alpha_1y", label: "Median vs peers (1Y)", render: (_r, v) => toned(v as number, formatPp(v as number)), tooltip: "For each scheme, its 1-year return minus its peers' median; this is the house's median of those gaps. It measures skill rather than mix: an equity-heavy house no longer outranks a debt house just because equity rose." },
  { key: "beat_peers_pct", label: "Beat their peers", format: "pct", decimals: 0, tooltip: "Share of the house's ranked schemes that beat their peer median over 1 year." },
  { key: "median_alpha_90d", label: "Median vs peers (90D)", render: (_r, v) => toned(v as number | null, formatPp(v as number | null)), tooltip: "The same gap over the last 90 days: is the house's record holding up recently?" },
];

/** Peer-relative quadrants (see services/leaders.classify_quadrant). */
const QUADRANTS: { key: string; color: string; about: string }[] = [
  { key: "Ahead of peers, calmer", color: "#059669", about: "Returned more than its peers' median with less volatility than they had: the better return did not come from taking more risk." },
  { key: "Ahead of peers, bumpier", color: "#2563EB", about: "Returned more than its peers, but with a bumpier ride than theirs. Some of the lead may be the extra risk." },
  { key: "Behind peers, calmer", color: CHART_MUTED_COLOR, about: "Returned less than its peers, with a smoother ride. A defensive fund can land here in a rising market by design." },
  { key: "Behind peers, bumpier", color: "#DC2626", about: "Returned less than its peers and swung more than they did: more risk for less reward over this window." },
];
const QUADRANT_COLOR: Record<string, string> = Object.fromEntries(QUADRANTS.map((q) => [q.key, q.color]));

const ROTATION: { key: RotationQuadrant; color: string; about: string }[] = [
  { key: "Leading", color: "#059669", about: "Ahead of the market over the window, and still ahead over its closing stretch." },
  { key: "Weakening", color: "#d97706", about: "Ahead of the market over the window, but behind it lately: leadership that may be fading." },
  { key: "Lagging", color: "#DC2626", about: "Behind the market over the window, and still behind lately." },
  { key: "Improving", color: "#2563EB", about: "Behind the market over the window, but ahead of it lately: a category that may be turning." },
];

const DIAGNOSES: { label: string; about: string }[] = [
  { label: "Bottom 10% of its peers", about: "Beaten by at least 90% of the funds it competes with (same asset class, SEBI category and plan) over this window." },
  { label: "Bottom quarter of its peers", about: "In the worst 25% of its peer group, but not the worst 10%." },
  { label: "Falling with its category", about: "Behind its peers, but not in their bottom quarter, in a category whose median fund lost money. The category, more than the fund, is most of the story." },
  { label: "Slightly behind its peers", about: "Below its peers' median, but not in their bottom quarter, while the category itself rose." },
];

const MAIN_TABS = [
  { id: "pulse", label: "📊 Market Pulse & Macro Trends" },
  { id: "leaders", label: "🏆 Alpha Leaders & Laggards" },
  { id: "quadrant", label: "🎯 Risk-Reward 4-Quadrant" },
  { id: "rotation", label: "🔄 Category Rotation" },
  { id: "funds", label: "📋 All Funds" },
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
  const [activeMainTab, setActiveMainTab] = useState<MainTab>(MAIN_TABS.some((t) => t.id === initialTab) ? initialTab : "pulse");

  // Market Pulse controls
  const [horizonChoice, setHorizonChoice] = useState(() => searchParams.get("horizon") || "1 Month (30D)");
  const [scoreView, setScoreView] = useState("Best 10");
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
  const [alphaTable, setAlphaTable] = useState("Leaders");
  const [absDirection, setAbsDirection] = useState<"gainers" | "losers">("gainers");
  const [volLookback, setVolLookback] = useState<string>(() => searchParams.get("vol_lookback") || "1Y");
  const [oneRowPerFund, setOneRowPerFund] = useState(true);
  const [quadPick, setQuadPick] = useState(QUADRANTS[0].key);
  const [rotPick, setRotPick] = useState<RotationQuadrant | "All">("All");
  const [showSmallCats, setShowSmallCats] = useState(false);

  useUrlSync({
    tab: activeMainTab !== "pulse" ? activeMainTab : undefined,
    horizon: horizonChoice !== "1 Month (30D)" ? horizonChoice : undefined,
    catScope: catScope !== "All Asset Classes" ? catScope : undefined,
    broad_cat: leadersBroadCat !== "All Categories" ? leadersBroadCat : undefined,
    sub_cat: leadersSubCat !== "All Sub-Categories" ? leadersSubCat : undefined,
    subTab: leadersSubTab !== "alpha" ? leadersSubTab : undefined,
    vol_lookback: volLookback !== "1Y" ? volLookback : undefined,
  });

  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ["overview-stats", planType, optionType],
    queryFn: () => getOverviewStats(planType, optionType),
    refetchInterval: 60_000,
  });

  const { data: kpis } = useQuery({
    queryKey: ["overview-pulse-kpis", planType, optionType, start, end],
    queryFn: () => getPulseKpis(start, end, planType, optionType),
    enabled: !!start && !!end,
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
    queryKey: ["category-matrix", catParam, planType, optionType],
    queryFn: () => getCategoryMatrix(catParam, planType, optionType),
  });

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
    enabled: activeMainTab !== "pulse" && activeMainTab !== "funds",
    placeholderData: keepPreviousData,
  });

  const span = windowDays(kpis?.first_nav_date, kpis?.last_nav_date) ?? (start && end ? Math.round((new Date(end).getTime() - new Date(start).getTime()) / 86400000) : 0);
  const windowText = kpis?.first_nav_date && kpis?.last_nav_date ? `${formatDate(kpis.first_nav_date)} → ${formatDate(kpis.last_nav_date)}` : "";

  const ranked = kpis?.ranked_funds ?? 0;
  const advPct = ranked > 0 ? ((kpis?.advancers ?? 0) / ranked) * 100 : 0;

  const catMatrixFiltered = useMemo(() => {
    if (!catMatrix || !debouncedCatSearch.trim()) return catMatrix ?? [];
    const tokens = debouncedCatSearch.toLowerCase().match(/[a-z0-9]+/g) ?? [];
    return catMatrix.filter((row) => {
      const hay = `${row["Asset Class"]} ${row["Category"]}`.toLowerCase();
      return tokens.every((t) => hay.includes(t));
    });
  }, [catMatrix, debouncedCatSearch]);

  const horizonKey = HORIZON_OPTIONS[horizonChoice] ?? "30d";
  const assetBarFigure = useMemo(() => {
    const rows = (stats?.asset_dist ?? []).filter((r) => r.asset_class !== "Other");
    const med = rows.map((r) => (r[`med_${horizonKey}`] as number | null) ?? null);
    return {
      data: [
        {
          type: "bar",
          x: rows.map((r) => r.asset_class),
          y: med,
          marker: { color: rows.map((r) => ASSET_CLASS_COLORS[r.asset_class] ?? CHART_MUTED_COLOR) },
          text: med.map((v) => (v === null ? "" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`)),
          textposition: "outside",
          cliponaxis: false,
          customdata: rows.map((r) => [r[`avg_${horizonKey}`] ?? null, r[`n_${horizonKey}`] ?? 0]),
          hovertemplate: "<b>%{x}</b><br>Median fund: %{y:+.2f}%<br>Mean: %{customdata[0]:+.2f}%<br>Funds: %{customdata[1]:,}<extra></extra>",
        },
      ],
      layout: {
        title: { text: `Median fund return by asset class (${horizonChoice})` },
        showlegend: false,
        yaxis: { ticksuffix: "%", showgrid: true, zeroline: true },
        xaxis: { showgrid: false },
        height: 360,
        margin: { t: 50 },
      },
    };
  }, [stats, horizonKey, horizonChoice]);

  const trendFigure = useMemo(() => {
    const byClass = new Map<string, { x: string[]; y: number[]; n: number[] }>();
    for (const r of trend ?? []) {
      const cls = r["Asset Class"];
      if (!byClass.has(cls)) byClass.set(cls, { x: [], y: [], n: [] });
      const s = byClass.get(cls)!;
      s.x.push(r.nav_date);
      s.y.push(r["Indexed Performance"]);
      s.n.push(r.Funds);
    }
    return {
      data: Array.from(byClass.entries()).map(([name, { x, y, n }]) => ({
        type: "scatter",
        mode: "lines",
        name,
        x,
        y,
        customdata: n,
        line: { color: ASSET_CLASS_COLORS[name] ?? CHART_MUTED_COLOR, width: 2 },
        hovertemplate: `${name}: <b>%{y:.2f}</b> (%{customdata:,} funds)<extra></extra>`,
      })),
      layout: {
        height: 360,
        hovermode: "x unified",
        hoversort: "value descending",
        xaxis: { showgrid: true },
        yaxis: { showgrid: true },
        shapes: [{ type: "line", x0: 0, x1: 1, xref: "paper", y0: 100, y1: 100, line: { dash: "dot", color: CHART_MUTED_COLOR, width: 1 } }],
        legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "right", x: 1 },
      },
    };
  }, [trend]);

  const amcAumFigure = useMemo(() => {
    const top = (stats?.amc_aum ?? []).slice(0, 12).reverse();
    return {
      data: [
        {
          type: "bar",
          orientation: "h",
          x: top.map((r) => r.share_pct),
          y: top.map((r) => r.fund_house.replace(/ Mutual Fund$/, "")),
          marker: { color: "#2a78d6" },
          text: top.map((r) => `${r.share_pct.toFixed(1)}% · ${formatCrore(r.aum_cr)}`),
          textposition: "outside",
          cliponaxis: false,
          customdata: top.map((r) => r.funds),
          hovertemplate: "<b>%{y}</b><br>%{x:.2f}% of industry assets<br>%{customdata} funds<extra></extra>",
        },
      ],
      layout: { height: 420, showlegend: false, margin: { l: 10, r: 120, t: 10 }, yaxis: { automargin: true }, xaxis: { ticksuffix: "%", showgrid: true } },
    };
  }, [stats]);

  const scorecardRows = useMemo(() => {
    const all: AmcScoreRow[] = stats?.amc_scorecard ?? [];
    if (scoreView === "Best 10") return all.slice(0, 10);
    if (scoreView === "Worst 10") return all.slice(-10).reverse();
    return all;
  }, [stats, scoreView]);

  const catMatrixColumns: ColumnConfig[] = [
    { key: "Asset Class", label: "Asset class" },
    { key: "Category", label: "Category", tooltip: "SEBI category. AMFI still files some funds under pre-2018 names ('Income/Debt Oriented Schemes - Liquid Fund'); those are counted with their modern category. Index Funds and ETFs are split by what they track." },
    { key: "Schemes", label: "Funds", format: "number", decimals: 0, tooltip: "Live Growth-type plans in the category." },
    { key: "AUM (Rs cr)", label: "AUM", render: (_r, v) => formatCrore(v as number | null), tooltip: "The category's assets as AMFI reports them." },
    { key: "Avg TER %", label: "Avg TER %", render: (_r, v) => formatTer(v as number | null), tooltip: "Mean total expense ratio across the category's plans, Direct and Regular together." },
    { key: "Median 1D %", label: "1D", render: (_r, v) => signed2(v), tooltip: "The median fund's trailing return over each window, as of the latest NAV date." },
    { key: "Median 7D %", label: "7D", render: (_r, v) => signed2(v) },
    { key: "Median 30D %", label: "30D", render: (_r, v) => signed2(v) },
    { key: "Median 90D %", label: "90D", render: (_r, v) => signed2(v) },
    { key: "Median 1Y %", label: "1Y", render: (r, v) => <span title={`${r["Funds with 1Y"]} funds are a year old`}>{signed2(v)}</span>, tooltip: "Only funds at least a year old have a 1-year return; hover a value for how many." },
    { key: "Median 52W High Gap %", label: "From 52W high", render: (_r, v) => signed2(v), tooltip: "How far the median fund's NAV sits below its highest point of the last 52 weeks." },
    {
      key: "Top Fund (30D) %", label: "Best fund (30D)", tooltip: "The category's best single fund over 30 days, named.",
      render: (r, v) => (v == null ? "-" : <span title={String(r["Top Fund (30D)"] ?? "")}>{toned(v as number, signed2(v))}<span className="block max-w-[14rem] truncate text-[0.7rem]" style={{ color: "var(--mf-muted)" }}>{String(r["Top Fund (30D)"] ?? "")}</span></span>),
    },
    {
      key: "Bottom Fund (30D) %", label: "Worst fund (30D)",
      render: (r, v) => (v == null ? "-" : <span title={String(r["Bottom Fund (30D)"] ?? "")}>{toned(v as number, signed2(v))}<span className="block max-w-[14rem] truncate text-[0.7rem]" style={{ color: "var(--mf-muted)" }}>{String(r["Bottom Fund (30D)"] ?? "")}</span></span>),
    },
  ];

  // --- Leaders ---------------------------------------------------------------------
  const rawLeaderRows = useMemo(() => leadersData?.rows ?? [], [leadersData]);
  const dedupe = oneRowPerFund && planType === "All Plans";
  const leaderRows = useMemo(() => (dedupe ? onePerFund(rawLeaderRows) : rawLeaderRows), [rawLeaderRows, dedupe]);
  const ambiguous = useMemo(() => ambiguousCategories(rawLeaderRows), [rawLeaderRows]);
  const peerLabel = (r: Record<string, unknown>) => peerGroupLabel(r.peer_category as string, r.asset_class as string, ambiguous);
  const withAlpha = useMemo(() => leaderRows.filter((r) => r.cat_alpha_pct !== null && r.cat_alpha_pct !== undefined), [leaderRows]);

  const alphaGainers = useMemo(() => [...withAlpha].sort((a, b) => (b.cat_alpha_pct as number) - (a.cat_alpha_pct as number)).slice(0, leadersTopN), [withAlpha, leadersTopN]);
  const alphaLosers = useMemo(() => [...withAlpha].sort((a, b) => (a.cat_alpha_pct as number) - (b.cat_alpha_pct as number)).slice(0, leadersTopN), [withAlpha, leadersTopN]);
  const absGainers = useMemo(() => [...leaderRows].sort((a, b) => b.period_return_pct - a.period_return_pct).slice(0, leadersTopN), [leaderRows, leadersTopN]);
  const absLosers = useMemo(() => [...leaderRows].sort((a, b) => a.period_return_pct - b.period_return_pct).slice(0, leadersTopN), [leaderRows, leadersTopN]);

  // --- Risk-reward quadrant (against each fund's own peers) ---------------------------
  const quadRows = useMemo(() => leaderRows.filter((r) => r.quadrant && r.cat_alpha_pct != null && r.vol_vs_peers_pct != null), [leaderRows]);
  const quadCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const r of quadRows) counts[r.quadrant as string] = (counts[r.quadrant as string] ?? 0) + 1;
    return counts;
  }, [quadRows]);
  const quadByClass = useMemo(() => {
    const m = new Map<string, Record<string, number>>();
    for (const r of quadRows) {
      if (!m.has(r.asset_class)) m.set(r.asset_class, {});
      const c = m.get(r.asset_class)!;
      c[r.quadrant as string] = (c[r.quadrant as string] ?? 0) + 1;
    }
    return Array.from(m.entries()).map(([asset_class, c]) => {
      const total = Object.values(c).reduce((a, b) => a + b, 0);
      return { asset_class, total, ...Object.fromEntries(QUADRANTS.map((q) => [q.key, total ? ((c[q.key] ?? 0) / total) * 100 : 0])) };
    }).sort((a, b) => b.total - a.total);
  }, [quadRows]);
  const quadPicked = useMemo(() => {
    const ahead = quadPick.startsWith("Ahead");
    return quadRows
      .filter((r) => r.quadrant === quadPick)
      .sort((a, b) => (ahead ? (b.cat_alpha_pct as number) - (a.cat_alpha_pct as number) : (a.cat_alpha_pct as number) - (b.cat_alpha_pct as number)))
      .slice(0, 100);
  }, [quadRows, quadPick]);

  // --- Category rotation ---------------------------------------------------------------
  const { data: rotation, isLoading: rotationLoading } = useQuery({
    queryKey: ["rotation", start, end, planType, optionType, leadersBroadCat],
    queryFn: () =>
      getRotation({
        start,
        end,
        broad_cat: leadersBroadCat !== "All Categories" ? leadersBroadCat : undefined,
        plan_type: planType !== "All Plans" ? planType : undefined,
        option_type: optionType !== "All Options" ? optionType : undefined,
      }),
    enabled: activeMainTab === "rotation" && !!start && !!end,
    placeholderData: keepPreviousData,
  });
  const rotAmbiguous = useMemo(() => ambiguousCategories((rotation?.categories ?? []).map((c) => ({ peer_category: c.category, asset_class: c.asset_class }))), [rotation]);
  const rotLabel = (c: { category: string; asset_class: string }) => peerGroupLabel(c.category, c.asset_class, rotAmbiguous);
  const rotationCats = useMemo(() => (rotation?.categories ?? []).filter((c) => showSmallCats || c.funds >= 5), [rotation, showSmallCats]);
  const rotationListed = useMemo(() => (rotPick === "All" ? rotationCats : rotationCats.filter((c) => c.quadrant === rotPick)), [rotationCats, rotPick]);

  const laggardRows = useMemo(
    () =>
      leaderRows
        .filter((r) => (r.cat_alpha_pct ?? 0) < 0)
        .sort((a, b) => (a.peer_percentile ?? 50) - (b.peer_percentile ?? 50) || (a.cat_alpha_pct as number) - (b.cat_alpha_pct as number))
        .slice(0, 100),
    [leaderRows]
  );
  const laggardCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const r of leaderRows) if ((r.cat_alpha_pct ?? 0) < 0) counts[r.diagnostic_classification] = (counts[r.diagnostic_classification] ?? 0) + 1;
    return counts;
  }, [leaderRows]);

  const common: Record<string, ColumnConfig> = {
    fund: { key: "display_name", label: "Fund", render: (_r, v) => <span className="font-semibold">{String(v)}</span> },
    peers: { key: "peer_category", label: "Peer group", sortValue: (r) => peerLabel(r), render: (r) => peerLabel(r), tooltip: "Funds it is ranked against: the same asset class, SEBI category and plan (Direct against Direct), priced across the whole window." },
    rank: { key: "peer_rank", label: "Rank in peers", sortValue: (r) => r.peer_percentile, render: (r) => (r.cat_alpha_pct == null ? <span title="Fewer than 5 peers to rank against">—</span> : `${r.peer_rank} of ${r.peer_count}`), tooltip: "1 = the best return in its peer group over the window." },
    ret: { key: "period_return_pct", label: "Return", render: (_r, v) => toned(v as number, signed2(v)), tooltip: "NAV change from the window's first to last NAV date." },
    peerMed: { key: "cat_median_return", label: "Peer median", render: (_r, v) => signed2(v), tooltip: "The median return of its peer group over the same window." },
    alpha: { key: "cat_alpha_pct", label: "vs peers", render: (_r, v) => toned(v as number | null, formatPp(v as number | null)), tooltip: "Return minus the peer median, in percentage points. Blank when there are fewer than 5 peers." },
    quart: { key: "quartile_rank", label: "Quartile", tooltip: "Which quarter of its peer group it finished in. Q1 = top 25%." },
    vol: { key: "annualized_vol_pct", label: "Volatility", render: (r, v) => (v == null ? "-" : <span title={`Annualised at ${r.obs_per_year ?? "?"} NAVs a year`}>{(v as number).toFixed(2)}%</span>), tooltip: "Annualised standard deviation of daily NAV moves over the volatility lookback, scaled by how often the fund actually prices (about 250 NAVs a year for equity, 365 for liquid funds)." },
    dd: { key: "max_drawdown_pct", label: "Max drawdown", render: (_r, v) => signed2(v), tooltip: "The worst fall from a peak to a later low inside the window." },
    high: { key: "dist_from_52w_high_pct", label: "From 52W high", render: (_r, v) => signed2(v), tooltip: "How far today's NAV is below its 52-week high. A trailing figure, independent of the window." },
    ter: { key: "expense_ratio", label: "TER %", render: (_r, v) => formatTer(v as number | null), tooltip: "Total expense ratio, as AMFI publishes it." },
    bench: {
      key: "excess_1y_pp", label: "1Y vs benchmark",
      render: (r, v) => (v == null ? "-" : <span title={`${r.benchmark}: fund ${formatSignedPct(r.official_1y_pct as number, 2)} vs benchmark ${formatSignedPct(r.benchmark_1y_pct as number, 2)} (AMFI)`}>{toned(v as number, formatPp(v as number))}</span>),
      tooltip: "AMFI's own 1-year return for this plan minus its official SEBI benchmark's, as published in AMFI's fund-performance data. A fixed trailing year, not the window. Hover for the benchmark's name.",
    },
    diag: { key: "diagnostic_classification", label: "Diagnosis", tooltip: "Why it is behind, from where it ranks among its peers. See the tiles above for each label." },
  };
  const ALPHA_COLUMNS = [common.rank, common.fund, common.peers, common.ret, common.peerMed, common.alpha, common.quart, common.vol, common.dd, common.ter, common.bench];
  const ABS_COLUMNS = [common.fund, common.peers, common.ret, common.alpha, common.dd, common.high, common.vol, common.ter];
  const LAGGARD_COLUMNS = [common.diag, common.rank, common.fund, common.peers, common.ret, common.peerMed, common.alpha, common.dd, common.high, common.bench];

  function hbar(rowsForChart: LeaderRow[], valueKey: "cat_alpha_pct" | "period_return_pct", title: string, unit: "pp" | "%") {
    const sorted = [...rowsForChart].reverse();
    const vals = sorted.map((r) => (r[valueKey] as number) ?? 0);
    return {
      data: [
        {
          type: "bar",
          orientation: "h",
          x: vals,
          y: sorted.map((r) => r.display_name),
          marker: { color: vals.map((v) => (v >= 0 ? "#10B981" : "#EF4444")) },
          text: vals.map((v) => (unit === "pp" ? formatPp(v) : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`)),
          textposition: "inside",
          customdata: sorted.map((r) => [peerLabel(r), r.peer_rank, r.peer_count, r.period_return_pct]),
          hovertemplate:
            unit === "pp"
              ? "<b>%{y}</b><br>%{x:+.2f} pp vs %{customdata[0]} peers<br>Return %{customdata[3]:+.2f}% · rank %{customdata[1]} of %{customdata[2]}<extra></extra>"
              : "<b>%{y}</b><br>%{x:+.2f}%<br>%{customdata[0]}<extra></extra>",
        },
      ],
      layout: { title: { text: title }, xaxis: { ticksuffix: unit === "pp" ? " pp" : "%", showgrid: true }, yaxis: { automargin: true }, height: Math.max(320, 180 + sorted.length * 24) },
    };
  }

  const quadX = useMemo(() => robustRange(quadRows.map((r) => r.vol_vs_peers_pct as number)), [quadRows]);
  const quadY = useMemo(() => robustRange(quadRows.map((r) => r.cat_alpha_pct as number)), [quadRows]);
  const quadOutside = useMemo(() => {
    if (!quadX || !quadY) return 0;
    return quadRows.filter((r) => {
      const x = r.vol_vs_peers_pct as number;
      const y = r.cat_alpha_pct as number;
      return x < quadX.range[0] || x > quadX.range[1] || y < quadY.range[0] || y > quadY.range[1];
    }).length;
  }, [quadRows, quadX, quadY]);
  const quadScatterFigure = useMemo(() => {
    return {
      data: QUADRANTS.map((q) => {
        const items = quadRows.filter((r) => r.quadrant === q.key);
        return {
          // WebGL: every placed fund is drawn, not the first 500 rows of an arbitrary order.
          type: "scattergl",
          mode: "markers",
          name: `${q.key} (${items.length})`,
          x: items.map((r) => r.vol_vs_peers_pct),
          y: items.map((r) => r.cat_alpha_pct),
          text: items.map((r) => r.display_name),
          customdata: items.map((r) => [peerLabel(r), r.period_return_pct, r.cat_median_return, r.annualized_vol_pct, r.peer_median_vol]),
          marker: { color: q.color, size: 6, opacity: 0.7 },
          hovertemplate:
            "<b>%{text}</b><br>Peers: %{customdata[0]}<br>Return %{customdata[1]:+.2f}% vs peer median %{customdata[2]:+.2f}% (%{y:+.2f} pp)" +
            "<br>Volatility %{customdata[3]:.2f}% vs peers' %{customdata[4]:.2f}% (%{x:+.0f}%)<extra></extra>",
        };
      }),
      layout: {
        height: 540,
        xaxis: { title: { text: "Volatility vs its peers (%): left = calmer" }, ticksuffix: "%", showgrid: true, zeroline: true, zerolinewidth: 2, range: quadX?.range },
        yaxis: { title: { text: "Return vs its peers (pp): up = ahead" }, ticksuffix: " pp", showgrid: true, zeroline: true, zerolinewidth: 2, range: quadY?.range },
        legend: { orientation: "h", yanchor: "top", y: -0.15, xanchor: "left", x: 0 },
        margin: { t: 20 },
      },
    };
  }, [quadRows, quadX, quadY]);

  const rrgFigure = useMemo(() => {
    const cats = rotationCats.filter((c) => c.momentum_pp !== null);
    const byClass = new Map<string, RotationCategory[]>();
    for (const c of cats) {
      if (!byClass.has(c.asset_class)) byClass.set(c.asset_class, []);
      byClass.get(c.asset_class)!.push(c);
    }
    const maxAum = Math.max(1, ...cats.map((c) => c.aum_cr ?? 0));
    // Labels only on the categories holding the most money: sixty labels would be unreadable.
    const labelled = new Set([...cats].sort((a, b) => (b.aum_cr ?? 0) - (a.aum_cr ?? 0)).slice(0, 14).map((c) => `${c.asset_class}|${c.category}`));
    const xs = robustRange(cats.map((c) => c.strength_pp), 0.2);
    const ys = robustRange(cats.map((c) => c.momentum_pp as number), 0.2);
    const corner = (x: number, y: number, text: string, color: string, xanchor: string, yanchor: string) => ({
      xref: "paper", yref: "paper", x, y, text: `<b>${text}</b>`, showarrow: false, font: { color, size: 13 }, xanchor, yanchor, opacity: 0.8,
    });
    return {
      data: Array.from(byClass.entries()).map(([cls, items]) => ({
        type: "scatter",
        mode: "markers+text",
        name: cls,
        x: items.map((c) => c.strength_pp),
        y: items.map((c) => c.momentum_pp),
        text: items.map((c) => (labelled.has(`${c.asset_class}|${c.category}`) ? rotLabel(c) : "")),
        textposition: "top center",
        textfont: { size: 10 },
        customdata: items.map((c) => [rotLabel(c), c.median_return, c.recent_median_return, c.funds, formatCrore(c.aum_cr)]),
        marker: {
          color: ASSET_CLASS_COLORS[cls] ?? CHART_MUTED_COLOR,
          size: items.map((c) => 10 + 34 * Math.sqrt((c.aum_cr ?? 0) / maxAum)),
          opacity: 0.75,
          line: { width: 1, color: "#fff" },
        },
        hovertemplate:
          "<b>%{customdata[0]}</b><br>Window: %{customdata[1]:+.2f}% (%{x:+.2f} pp vs market)" +
          `<br>Last ${rotation?.recent_days ?? ""} days: %{customdata[2]:+.2f}% (%{y:+.2f} pp vs market)` +
          "<br>%{customdata[3]} funds · %{customdata[4]}<extra></extra>",
      })),
      layout: {
        height: 560,
        xaxis: { title: { text: "Strength over the window vs the market (pp)" }, ticksuffix: " pp", zeroline: true, zerolinewidth: 2, showgrid: true, range: xs?.range },
        yaxis: { title: { text: `Momentum: last ${rotation?.recent_days ?? ""} days vs the market (pp)` }, ticksuffix: " pp", zeroline: true, zerolinewidth: 2, showgrid: true, range: ys?.range },
        legend: { orientation: "h", yanchor: "top", y: -0.15, xanchor: "left", x: 0 },
        margin: { t: 20 },
        annotations: [
          corner(0.99, 0.99, "Leading", "#059669", "right", "top"),
          corner(0.99, 0.01, "Weakening", "#d97706", "right", "bottom"),
          corner(0.01, 0.01, "Lagging", "#DC2626", "left", "bottom"),
          corner(0.01, 0.99, "Improving", "#2563EB", "left", "top"),
        ],
      },
    };
  }, [rotationCats, rotation?.recent_days]);

  const heatFigure = useMemo(() => {
    const cats = [...rotationCats].sort((a, b) => b.median_return - a.median_return);
    const periods = rotation?.periods ?? [];
    const x = periods.map((p) => periodLabel(p, rotation?.unit));
    const y = ["Market (median fund)", ...cats.map((c) => rotLabel(c))];
    const z = [rotation?.market_period_median ?? [], ...cats.map((c) => c.heat)];
    const all = z.flat().filter((v): v is number => v !== null && Number.isFinite(v));
    // Symmetric around zero so a colour means the same size of move up or down.
    const lim = all.length ? Math.max(...all.map(Math.abs).sort((a, b) => a - b).slice(0, Math.max(1, Math.floor(all.length * 0.98)))) : 1;
    return {
      data: [
        {
          type: "heatmap",
          x,
          y,
          z,
          zmin: -lim,
          zmax: lim,
          zmid: 0,
          colorscale: [[0, "#b91c1c"], [0.5, "#f8fafc"], [1, "#047857"]],
          text: z.map((row) => row.map((v) => (v === null || v === undefined ? "" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}`))),
          texttemplate: "%{text}",
          textfont: { size: 9 },
          hovertemplate: "<b>%{y}</b><br>%{x}: %{z:+.2f}%<extra></extra>",
          colorbar: { ticksuffix: "%", thickness: 10 },
          xgap: 1,
          ygap: 1,
        },
      ],
      layout: {
        height: Math.max(320, 110 + y.length * 22),
        yaxis: { autorange: "reversed", automargin: true, dtick: 1 },
        xaxis: { side: "top" },
        margin: { t: 40 },
      },
    };
  }, [rotationCats, rotation]);

  const assetClassOptions = leadersData?.asset_classes ?? [];
  const subCatOptions = useMemo(() => {
    const byClass = leadersData?.categories_by_class ?? {};
    const cats = leadersBroadCat !== "All Categories" ? byClass[leadersBroadCat] ?? [] : Array.from(new Set(Object.values(byClass).flat())).sort();
    return cats;
  }, [leadersData, leadersBroadCat]);

  const tabButton = (id: "alpha" | "abs" | "laggard", label: string) => (
    <button
      type="button"
      onClick={() => setLeadersSubTab(id)}
      className="rounded-full px-3 py-1 text-xs font-semibold"
      style={{ background: leadersSubTab === id ? "var(--mf-accent-bg)" : "transparent", color: leadersSubTab === id ? "var(--mf-accent)" : "var(--mf-fg)" }}
    >
      {label}
    </button>
  );

  return (
    <AppShell>
      {/* --- Executive Header --- */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="mf-page-title">Executive Market Overview & Alpha Intelligence</h1>
          <p className="mf-page-caption">
            How the mutual fund market moved, where the money is, and which funds are beating the funds they compete with.
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
          title="Schemes publishing NAVs"
          value={statsLoading ? "..." : (stats?.active_schemes ?? 0).toLocaleString()}
          sub={statsLoading ? "" : `${stats?.total_amcs ?? 0} fund houses · ${(stats?.total_schemes ?? 0).toLocaleString()} ever listed`}
          tooltip={<FormulaTooltip label="Schemes publishing NAVs" description="Live schemes: every plan and option counts separately (a fund's Direct-Growth and Regular-IDCW are two schemes). Ever listed includes matured FMPs and wound-up schemes." />}
        />
        <StatCard
          title="Industry assets (AUM)"
          value={stats?.aum_total_cr ? formatCrore(stats.aum_total_cr) : "-"}
          sub={stats?.aum_as_of ? `AMFI, as of ${formatDate(stats.aum_as_of)}` : "Syncs with the nightly AMFI refresh"}
          tooltip={<FormulaTooltip label="Industry assets" description="Assets under management of every open-ended fund, as AMFI's fund-performance data reports them (in Rs crore, one figure per fund across all its plans). 1 lakh crore = 1,00,000 crore." />}
        />
        <StatCard
          title={`Market breadth (${span}D)`}
          value={kpis ? `${advPct.toFixed(1)}%` : "..."}
          sub={kpis ? `Up ${kpis.advancers.toLocaleString()} · Down ${kpis.decliners.toLocaleString()} of ${ranked.toLocaleString()}` : ""}
          tone={toneOf((kpis?.advancers ?? 0) - (kpis?.decliners ?? 0))}
          tooltip={<FormulaTooltip label="Market breadth" description={`Share of funds whose NAV rose over the window${windowText ? ` (${windowText})` : ""}. Counts Growth-type funds priced across the whole window; IDCW plans are left out because a payout lowers the NAV and would count as a fall.`} />}
        />
        <StatCard
          title={`Median fund (${span}D)`}
          value={formatSignedPct(kpis?.median_return ?? null, 2)}
          sub={`Mean ${formatSignedPct(kpis?.mean_return ?? null, 2)}`}
          tone={toneOf(kpis?.median_return ?? null)}
          tooltip={<FormulaTooltip label="Median fund" description="The middle fund's return over the window, among the same funds as Market breadth. The mean is shown beside it: a gap between the two means a few funds moved a lot." />}
        />
        <StatCard
          title="Top fund vs its peers"
          value={kpis?.top_alpha ? formatPp(kpis.top_alpha.return_pct) : "-"}
          sub={kpis?.top_alpha?.name ?? "No data"}
          tone={kpis?.top_alpha ? toneOf(kpis.top_alpha.return_pct) : "neutral"}
          tooltip={<FormulaTooltip label="Top fund vs its peers" description="The fund furthest ahead of its own peer group's median return over the window (same asset class, SEBI category and plan; 5+ peers). A gap in percentage points, not its raw return. This tile covers the whole market whatever the Leaders tab is filtered to." />}
        />
        <StatCard
          title={`Leading category (${span}D)`}
          value={kpis?.leading_category?.name ?? "N/A"}
          sub={kpis?.leading_category ? `Median ${formatSignedPct(kpis.leading_category.return_pct, 2)} · ${kpis.leading_category.funds} funds${kpis.lagging_category ? ` · Weakest: ${kpis.lagging_category.name} ${formatSignedPct(kpis.lagging_category.return_pct, 2)}` : ""}` : ""}
          subTone={(kpis?.leading_category?.return_pct ?? 0) >= 0 ? "pos" : "neg"}
          tooltip={<FormulaTooltip label="Leading category" description="The category whose median fund did best over the window, among categories with at least 5 distinct funds. A fund's Direct and Regular plans count as one fund." />}
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

      {/* TAB 1: MARKET PULSE & MACRO TRENDS */}
      {activeMainTab === "pulse" && (
        <div className="mt-6 space-y-8">
          <Card
            title="Asset classes"
            about="Funds grouped by where their money is: Equity, International Equity, Hybrid, Debt, Cash & Liquid (liquid, overnight, money-market and arbitrage funds), Gold & Commodities. Returns are the median fund's, over Growth-type plans; the mean is in the hover."
            caption={
              <>
                Trailing returns as of the latest NAV date{stats?.max_date ? ` (${formatDate(stats.max_date)})` : ""}; they do not move with the date picker. Over {(stats?.returns_pool ?? 0).toLocaleString()} Growth-type plans:{" "}
                {(stats?.idcw_excluded ?? 0).toLocaleString()} IDCW plans are left out, since each payout drops their NAV and would read as a loss.
              </>
            }
            right={<PillRadio options={Object.keys(HORIZON_OPTIONS)} value={horizonChoice} onChange={setHorizonChoice} />}
          >
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
              <div className="lg:col-span-2">
                <PlotlyChart figure={assetBarFigure} />
              </div>
              <div className="lg:col-span-3">
                <DataTable keyField="asset_class" columns={ASSET_TABLE_COLUMNS} rows={(stats?.asset_dist ?? []) as AssetDistRow[]} />
              </div>
            </div>
          </Card>

          <Card
            title="How each asset class moved (base = 100)"
            about="The average fund in each class, indexed to 100 at the start of the date window: each fund is divided by its own first NAV in the window and those ratios are averaged, so a Rs 3,000 NAV counts the same as a Rs 10 one. The basket is fixed to funds priced when the window opens, so launches and closures do not move the line. Days on which fewer than 60% of a class priced (weekends for most classes; the day overseas funds have not yet reported) are skipped."
            caption={windowText ? `Growth-type plans, ${windowText}.` : "Growth-type plans over the selected window."}
          >
            {trend && trend.length > 3 ? <PlotlyChart figure={trendFigure} /> : <Banner level="info">Too few NAV dates in this range to draw the lines.</Banner>}
          </Card>

          <Card
            title="Fund houses"
            about="Left: the largest AMCs by the assets they manage, from AMFI's reported AUM. Right: the same houses scored on skill. Each scheme's 1-year return is compared with its own peers (same asset class, SEBI category and plan), and a house is scored by the median of those gaps. Mix does not help: running more equity funds in a rising market does not raise the score."
            caption={stats?.aum_as_of ? `AUM as AMFI reported it on ${formatDate(stats.aum_as_of)}. Scores over trailing 1-year returns as of ${formatDate(stats.max_date)}.` : undefined}
          >
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div>
                <h3 className="flex items-center text-base font-bold">
                  Largest by assets
                  <FormulaTooltip label="Largest by assets" description="Share of the industry's open-ended AUM managed by each house: the true market share. The old chart counted schemes, where every Direct/Regular/Growth/IDCW variant counted separately and AUM was ignored." />
                </h3>
                {stats && stats.amc_aum.length > 0 ? (
                  <PlotlyChart figure={amcAumFigure} />
                ) : (
                  <Banner level="info">No AUM yet. It arrives with the nightly AMFI refresh.</Banner>
                )}
              </div>
              <div>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="flex items-center text-base font-bold">
                    Scored against their peers
                    <FormulaTooltip label="Scored against their peers" description={`Houses with at least 5 schemes that can be ranked (a year old, 5+ peers). ${stats?.amc_scorecard.length ?? 0} houses qualify.`} />
                  </h3>
                  <PillRadio options={["Best 10", "Worst 10", "All"]} value={scoreView} onChange={setScoreView} />
                </div>
                <div className="mt-2">
                  {statsLoading ? <Banner level="info">Loading...</Banner> : <DataTable keyField="fund_house" columns={SCORECARD_COLUMNS} rows={scorecardRows} />}
                </div>
              </div>
            </div>
          </Card>

          <Card
            title="Category performance matrix"
            about="Every SEBI category, one row each: how many live funds, their assets, the typical cost and the median fund's trailing returns. Also shown: how far the median fund sits below its 52-week high, and the best and worst single funds over 30 days."
            caption="Trailing windows as of the latest NAV date, independent of the date picker. Over Growth-type plans; 1Y only counts funds at least a year old."
          >
            <div className="filter-box flex flex-wrap items-center gap-4">
              <PillRadio options={["All Asset Classes", ...(stats?.asset_classes ?? [])]} value={catScope} onChange={setCatScope} />
              <input
                type="text"
                placeholder="Filter categories in any order (e.g. small cap, liquid)..."
                value={catSearch}
                onChange={(e) => setCatSearch(e.target.value)}
                className="min-w-[260px] flex-1 rounded-lg border px-3 py-1.5 text-sm"
                style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
              />
            </div>
            <div className="mt-3">
              <DataTable keyField="Category" columns={catMatrixColumns} rows={catMatrixFiltered as CategoryMatrixRow[]} />
            </div>
          </Card>
        </div>
      )}

      {/* Fund filters: shared by Alpha Leaders and the Risk-Reward quadrant */}
      {(activeMainTab === "leaders" || activeMainTab === "quadrant") && (
        <div className="mt-6 space-y-4">
          <div className="filter-box">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-4 lg:grid-cols-6">
              <Select
                label="Asset class"
                tip="Narrows the list. It never changes a fund's rank: peers are always the whole market's funds in its category."
                value={leadersBroadCat}
                onChange={(v) => {
                  setLeadersBroadCat(v);
                  setLeadersSubCat("All Sub-Categories");
                }}
                options={[{ value: "All Categories", label: "All asset classes" }, ...assetClassOptions]}
              />
              <Select
                label="Peer category"
                value={leadersSubCat}
                onChange={setLeadersSubCat}
                options={[{ value: "All Sub-Categories", label: "All categories" }, ...subCatOptions]}
              />
              <Select label="Plan (global)" value={planType} onChange={(v) => setPlanType(v as never)} options={["All Plans", "Direct", "Regular"]} />
              <Select
                label="Option (global)"
                tip="IDCW plans are ranked only when IDCW is chosen, against each other: a payout lowers the NAV and would otherwise read as a loss."
                value={optionType}
                onChange={(v) => setOptionType(v as never)}
                options={["All Options", "Growth", "IDCW"]}
              />
              <Select label="Show top N" value={String(leadersTopN)} onChange={(v) => setLeadersTopN(Number(v))} options={["5", "10", "15", "20", "25", "50"]} />
              <Select
                label="Volatility lookback"
                tip="How much history the volatility figure uses, capped at the date window. 1Y (default) measures the current regime. Full window uses the whole selected range."
                value={volLookback}
                onChange={setVolLookback}
                options={["1Y", "6M", "3Y", "Full Window"]}
              />
              <div className="col-span-1 sm:col-span-2 md:col-span-2 lg:col-span-4">
                <div className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
                  <span>Search fund name (any word order)</span>
                  <SearchCombobox
                    placeholder="Search fund name or AMFI code..."
                    value={leadersSearch}
                    onChangeQuery={setLeadersSearch}
                    onClear={() => setLeadersSearch("")}
                    extraParams={{
                      plan_type: planType !== "All Plans" ? planType : undefined,
                      option_type: optionType !== "All Options" ? optionType : undefined,
                    }}
                    onSelect={(scheme) => setLeadersSearch(scheme.scheme_name)}
                  />
                </div>
              </div>
              <label className="col-span-1 flex items-end gap-2 pb-1.5 text-xs font-medium sm:col-span-2" style={{ color: "var(--mf-muted)" }}>
                <input type="checkbox" checked={oneRowPerFund} disabled={planType !== "All Plans"} onChange={(e) => setOneRowPerFund(e.target.checked)} />
                One row per fund (Direct plan)
                <FormulaTooltip label="One row per fund" description="A fund's Direct and Regular plans hold the same portfolio. Listed separately, one fund takes two slots in every top-N. The Regular plan still ranks, against Regular peers. Pick Regular in the Plan filter to see it." />
              </label>
            </div>
          </div>

          {leadersData && (
            <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
              Ranking {rawLeaderRows.length.toLocaleString()} schemes priced across the whole window
              {leadersData.window.first_nav_date ? ` (${formatDate(leadersData.window.first_nav_date)} → ${formatDate(leadersData.window.last_nav_date)})` : ""}. Left out:{" "}
              {leadersData.excluded_idcw.toLocaleString()} IDCW plans, {leadersData.excluded_partial_window.toLocaleString()} funds that launched or stopped pricing inside the window
              {leadersData.excluded_thin_data ? `, ${leadersData.excluded_thin_data} with under 5 NAVs` : ""}.
              <FormulaTooltip label="Who is ranked" description="A fund launched a week into a 30-day window would otherwise compete on 23 days, and an IDCW plan's payout would read as a loss. Peer groups are the same asset class, SEBI category and plan, so Direct plans are ranked against Direct and Regular against Regular." />
            </p>
          )}
        </div>
      )}

      {/* TAB 2: ALPHA LEADERS & LAGGARDS */}
      {activeMainTab === "leaders" && (
        <div className="mt-4 space-y-6">
          <div className="flex flex-wrap gap-2 border-b pb-2" style={{ borderColor: "var(--mf-border)" }}>
            {tabButton("alpha", "Beat their peers")}
            {tabButton("abs", "Biggest movers")}
            {tabButton("laggard", "Laggard diagnostics")}
          </div>

          <div className="min-h-[600px]">
            {leadersLoading && !leadersData ? (
              <p className="text-sm" style={{ color: "var(--mf-muted)" }}>Ranking funds across {span} days...</p>
            ) : leaderRows.length === 0 ? (
              <Banner level="warning">No funds match these filters in this window.</Banner>
            ) : (
              <>
                {leadersSubTab === "alpha" && (
                  <div className="space-y-6">
                    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                      <div className="rounded-xl border p-4" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
                        <PlotlyChart figure={hbar(alphaGainers, "cat_alpha_pct", `Top ${leadersTopN}: furthest ahead of their peers`, "pp")} />
                      </div>
                      <div className="rounded-xl border p-4" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
                        <PlotlyChart figure={hbar(alphaLosers, "cat_alpha_pct", `Bottom ${leadersTopN}: furthest behind their peers`, "pp")} />
                      </div>
                    </div>
                    <div>
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <h3 className="flex items-center text-base font-bold">
                          {alphaTable === "Leaders" ? "Leaders" : "Laggards"} against their peers
                          <FormulaTooltip label="Against their peers" description="Ranked by the gap between the fund's return and its peer group's median. Funds with fewer than 5 peers are not ranked. The last column is AMFI's own 1-year figure against the fund's official SEBI benchmark: a second opinion from a different yardstick." />
                        </h3>
                        <PillRadio options={["Leaders", "Laggards"]} value={alphaTable} onChange={setAlphaTable} />
                      </div>
                      <div className="mt-2">
                        <DataTable columns={ALPHA_COLUMNS} rows={alphaTable === "Leaders" ? alphaGainers : alphaLosers} keyField="scheme_code" />
                      </div>
                    </div>
                  </div>
                )}

                {leadersSubTab === "abs" && (
                  <div className="space-y-6">
                    <div className="flex items-center gap-2">
                      <PillRadio options={["Top returns", "Weakest returns"]} value={absDirection === "gainers" ? "Top returns" : "Weakest returns"} onChange={(v) => setAbsDirection(v === "Top returns" ? "gainers" : "losers")} />
                      <FormulaTooltip label="Biggest movers" description="Raw returns over the window, whatever the category. These lists are mostly about which asset class moved: a gold fund tops them when gold rallies. For skill, see Beat their peers." />
                    </div>
                    <div className="rounded-xl border p-4" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
                      <PlotlyChart figure={hbar(absDirection === "gainers" ? absGainers : absLosers, "period_return_pct", `${absDirection === "gainers" ? "Top" : "Weakest"} ${leadersTopN} returns over the window`, "%")} />
                    </div>
                    <DataTable columns={ABS_COLUMNS} rows={absDirection === "gainers" ? absGainers : absLosers} keyField="scheme_code" />
                  </div>
                )}

                {leadersSubTab === "laggard" && (
                  <div className="space-y-6">
                    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                      {DIAGNOSES.map((d) => (
                        <StatCard key={d.label} title={d.label} value={`${(laggardCounts[d.label] ?? 0).toLocaleString()} funds`} tooltip={<FormulaTooltip label={d.label} description={d.about} />} />
                      ))}
                    </div>
                    <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
                      Funds behind their peers&apos; median over the window, worst-ranked first (up to 100). Diagnosed by rank rather than fixed thresholds. A 3-point gap is a disaster
                      for a liquid fund over a month and ordinary for a small-cap fund over three years.
                    </p>
                    <DataTable columns={LAGGARD_COLUMNS} rows={laggardRows} keyField="scheme_code" />
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* TAB 3: RISK-REWARD 4-QUADRANT MATRIX */}
      {activeMainTab === "quadrant" && (
        <div className="mt-4 space-y-6">
          {leadersLoading && !leadersData ? (
            <p className="text-sm" style={{ color: "var(--mf-muted)" }}>Placing funds against their peers...</p>
          ) : quadRows.length === 0 ? (
            <Banner level="info">No fund in this selection has 5 or more peers with a measured volatility, so none can be placed.</Banner>
          ) : (
            <>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {QUADRANTS.map((q) => (
                  <QuadrantTile
                    key={q.key}
                    label={q.key}
                    count={quadCounts[q.key] ?? 0}
                    share={quadRows.length ? ((quadCounts[q.key] ?? 0) / quadRows.length) * 100 : null}
                    color={q.color}
                    about={q.about}
                    active={quadPick === q.key}
                    onClick={() => setQuadPick(q.key)}
                  />
                ))}
              </div>

              <Card
                title="Return and risk, each against its own peers"
                about="Each dot is a fund. Up/down: its return over the window minus its peer group's median return (pp). Left/right: its volatility against its peers' median volatility (%). Peers are the same asset class, SEBI category and plan. Measuring against the whole market instead only sorted funds by asset class: liquid funds all looked calm and equity funds all looked risky. Here a liquid fund is judged against liquid funds."
                caption={
                  <>
                    {quadRows.length.toLocaleString()} funds placed ({windowText || `${span} days`}; volatility over the {volLookback} lookback). The cross at 0/0 is the peer median on both counts.
                    {quadOutside > 0 ? ` ${quadOutside} extreme funds sit outside this view; double-click the chart to zoom out.` : ""} Descriptive, not a recommendation.
                  </>
                }
              >
                <PlotlyChart figure={quadScatterFigure} />
              </Card>

              <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
                <div className="xl:col-span-2">
                  <h3 className="flex items-center text-base font-bold">
                    {quadPick}
                    <FormulaTooltip label={quadPick} description={`${QUADRANTS.find((q) => q.key === quadPick)?.about ?? ""} Pick another tile above to list its funds. Sorted by how far ${quadPick.startsWith("Ahead") ? "ahead of" : "behind"} their peers they are; up to 100 shown.`} />
                  </h3>
                  <div className="mt-2">
                    <DataTable
                      columns={[
                        common.fund,
                        common.peers,
                        common.ret,
                        common.alpha,
                        common.vol,
                        { key: "peer_median_vol", label: "Peers' volatility", render: (_r, v) => (v == null ? "-" : `${(v as number).toFixed(2)}%`), tooltip: "The median annualised volatility of its peer group over the same lookback." },
                        { key: "vol_vs_peers_pct", label: "Vol vs peers", render: (_r, v) => (v == null ? "-" : `${(v as number) > 0 ? "+" : ""}${(v as number).toFixed(0)}%`), tooltip: "Its volatility relative to its peers' median: -20% means 20% calmer than the typical peer." },
                        common.dd,
                        common.ter,
                      ]}
                      rows={quadPicked}
                      keyField="scheme_code"
                    />
                  </div>
                </div>
                <div>
                  <h3 className="flex items-center text-base font-bold">
                    By asset class
                    <FormulaTooltip label="By asset class" description="Share of each class's placed funds in each quadrant. Because every fund is measured against its own peers, each class spreads across all four quadrants. Under the old market-wide medians, 241 of 242 liquid funds landed in one box." />
                  </h3>
                  <div className="mt-2">
                    <DataTable
                      keyField="asset_class"
                      columns={[
                        { key: "asset_class", label: "Asset class" },
                        { key: "total", label: "Funds", format: "number", decimals: 0 },
                        ...QUADRANTS.map<ColumnConfig>((q) => ({ key: q.key, label: q.key.replace(" of peers", "").replace("peers, ", ""), format: "pct", decimals: 0, tooltip: q.about })),
                      ]}
                      rows={quadByClass}
                    />
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* TAB 4: CATEGORY ROTATION */}
      {activeMainTab === "rotation" && (
        <div className="mt-6 space-y-6">
          <div className="filter-box flex flex-wrap items-end gap-4">
            <Select
              label="Asset class"
              tip="Narrows which categories are shown. The market they are measured against is always the whole market."
              value={leadersBroadCat}
              onChange={(v) => {
                setLeadersBroadCat(v);
                setLeadersSubCat("All Sub-Categories");
              }}
              options={[{ value: "All Categories", label: "All asset classes" }, ...(leadersData?.asset_classes ?? ["Equity", "International Equity", "Hybrid", "Debt", "Cash & Liquid", "Gold & Commodities"])]}
            />
            <Select label="Plan (global)" value={planType} onChange={(v) => setPlanType(v as never)} options={["All Plans", "Direct", "Regular"]} />
            <Select label="Option (global)" value={optionType} onChange={(v) => setOptionType(v as never)} options={["All Options", "Growth", "IDCW"]} />
            <label className="flex items-center gap-2 pb-1.5 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
              <input type="checkbox" checked={showSmallCats} onChange={(e) => setShowSmallCats(e.target.checked)} />
              Include categories with 3–4 funds
              <FormulaTooltip label="Small categories" description="A category's median over three or four funds is mostly those funds' own story. They are hidden by default; categories with fewer than 3 funds are never shown." />
            </label>
          </div>

          {rotationLoading && !rotation ? (
            <p className="text-sm" style={{ color: "var(--mf-muted)" }}>Measuring category rotation...</p>
          ) : !rotation || rotationCats.length === 0 ? (
            <Banner level="info">No categories with enough funds in this selection and window.</Banner>
          ) : (
            <>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {ROTATION.map((q) => {
                  const inQ = rotationCats.filter((c) => c.quadrant === q.key).sort((a, b) => (b.aum_cr ?? 0) - (a.aum_cr ?? 0));
                  return (
                    <QuadrantTile
                      key={q.key}
                      label={q.key}
                      count={inQ.length}
                      share={null}
                      color={q.color}
                      about={q.about}
                      active={rotPick === q.key}
                      onClick={() => setRotPick(rotPick === q.key ? "All" : q.key)}
                      sub={inQ.slice(0, 3).map((c) => rotLabel(c)).join(", ")}
                    />
                  );
                })}
              </div>

              <Card
                title="Which categories are gaining or losing leadership"
                about={`Each bubble is a category, sized by its assets (AUM). Right/left: its median fund's return over the window minus the whole market's median. Up/down: the same over the window's last ${rotation.recent_days} days. A category on the right that has dropped below the line was strong but has faded lately (Weakening). One on the left above the line has been ahead lately after a weak window (Improving). Each fund counts once (its Direct plan), and IDCW plans are left out.`}
                caption={`Market median: ${formatSignedPct(rotation.market_median, 2)} over the window, ${formatSignedPct(rotation.market_recent_median, 2)} over its last ${rotation.recent_days} days. Labels on the 14 largest categories by assets; hover for the rest.`}
              >
                <PlotlyChart figure={rrgFigure} />
              </Card>

              <Card
                title={rotation.unit === "month" ? "Rotation, month by month" : "Rotation, week by week"}
                about={`The median fund's return in each category, ${rotation.unit === "month" ? "month" : "week"} by ${rotation.unit === "month" ? "month" : "week"} (the first and last periods may be partial). Green = up, red = down; the colour scale is the same on both sides of zero. The top row is the whole market's median fund. Read across a row to see a category's run; read down a column to see which categories led that ${rotation.unit}.`}
                caption="Sorted by the category's return over the whole window, best first."
              >
                <PlotlyChart figure={heatFigure} />
              </Card>

              <Card
                title={rotPick === "All" ? "Every category" : `${rotPick} categories`}
                about="All the figures behind the charts. Click a tile above to show only its categories, and click it again to show all."
              >
                <DataTable
                  keyField="category"
                  rows={rotationListed.map((c) => ({ ...c, label: rotLabel(c) }))}
                  columns={[
                    { key: "asset_class", label: "Asset class" },
                    { key: "label", label: "Category" },
                    { key: "quadrant", label: "Rotation", render: (_r, v) => (v ? <span style={{ color: ROTATION.find((q) => q.key === v)?.color }}>{String(v)}</span> : "-"), tooltip: "Leading, Weakening, Lagging or Improving: see the tiles above." },
                    { key: "funds", label: "Funds", format: "number", decimals: 0, tooltip: "Distinct funds priced across the window (Direct and Regular plans count once)." },
                    { key: "aum_cr", label: "AUM", render: (_r, v) => formatCrore(v as number | null), tooltip: "The category's assets as AMFI reports them." },
                    { key: "median_return", label: "Window", render: (_r, v) => toned(v as number, signed2(v)), tooltip: "Median fund's return over the whole window." },
                    { key: "recent_median_return", label: `Last ${rotation.recent_days}D`, render: (_r, v) => toned(v as number | null, signed2(v)), tooltip: "Median fund's return over the window's closing stretch (its last third)." },
                    { key: "strength_pp", label: "Strength", render: (_r, v) => formatPp(v as number), tooltip: "Window median minus the market's window median." },
                    { key: "momentum_pp", label: "Momentum", render: (_r, v) => formatPp(v as number | null), tooltip: "Recent median minus the market's recent median." },
                    { key: "pct_up", label: "Funds up", format: "pct", decimals: 0, tooltip: "Share of the category's funds with a positive return over the window: how broad the move was." },
                    { key: "p25_return", label: "Middle half", sortValue: (r) => (r.p75_return as number) - (r.p25_return as number), render: (r) => `${signed2(r.p25_return)} to ${signed2(r.p75_return)}`, tooltip: "The range of the middle 50% of funds (25th to 75th percentile). Wide = picking the fund matters more than picking the category. Unlike max minus min, one odd fund cannot stretch it." },
                    { key: "median_vol", label: "Volatility", render: (_r, v) => (v == null ? "-" : `${(v as number).toFixed(2)}%`), tooltip: "Median fund's annualised volatility." },
                    { key: "median_drawdown", label: "Max drawdown", render: (_r, v) => signed2(v), tooltip: "Median fund's worst peak-to-trough fall inside the window." },
                    { key: "avg_ter", label: "Avg TER %", render: (_r, v) => formatTer(v as number | null), tooltip: "Mean expense ratio of the category's funds (Direct plans where a fund has one)." },
                  ]}
                />
              </Card>
            </>
          )}
        </div>
      )}

      {/* TAB 5: ALL FUNDS */}
      {activeMainTab === "funds" && <AllFundsTab planType={planType} optionType={optionType} />}
    </AppShell>
  );
}
