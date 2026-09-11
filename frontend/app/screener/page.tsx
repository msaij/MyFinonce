"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { AppShell } from "@/components/layout/AppShell";
import { StatCard } from "@/components/shared/StatCard";
import { DataTable, ColumnConfig } from "@/components/shared/DataTable";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { SearchCombobox } from "@/components/shared/SearchCombobox";
import { Banner } from "@/components/shared/Banner";
import { formatSignedPct, formatDate, toneOf } from "@/lib/format";
import { useDateRangeStore } from "@/lib/stores/dateRange";
import { useFilterStore } from "@/lib/stores/filters";
import { getMetaFilters } from "@/lib/api/meta";
import { getScreener, getScreenerKpis, type ScreenerFilterParams } from "@/lib/api/screener";
import { getNavHistory } from "@/lib/api/schemes";

const SECTION = "screener";

const SORT_OPTIONS: Record<string, string> = {
  "Selected Period Return %": "period_return_pct",
  "30-Day Return %": "return_30d_pct",
  "1-Year Return %": "return_1y_pct",
  "7-Day Return %": "return_7d_pct",
  "90-Day Return %": "return_90d_pct",
  "1-Day Change %": "change_1d_pct",
  "Lowest Expense Ratio (TER)": "expense_ratio",
  "Lowest Exit Load %": "exit_load_pct",
  "Latest NAV (Rs)": "latest_nav",
  "Scheme Name": "scheme_name",
  "Distance from 52W High %": "dist_from_52w_high_pct",
};

const TER_OPTIONS: Record<string, number | undefined> = {
  "All Expense Ratios": undefined,
  "<= 0.50% (Ultra-Low)": 0.5,
  "<= 1.00%": 1.0,
  "<= 1.50%": 1.5,
  "<= 2.00%": 2.0,
};

const SCREENER_TABLE_COLUMNS: ColumnConfig[] = [
  { key: "scheme_code", label: "AMFI Code", format: "number", decimals: 0 },
  { key: "scheme_name", label: "Scheme Name" },
  { key: "fund_house", label: "AMC" },
  { key: "category", label: "Category" },
  { key: "plan_type", label: "Plan" },
  { key: "option_type", label: "Option" },
  { key: "expense_ratio", label: "TER %", format: "signed_pct" },
  { key: "ter_status", label: "TER Confidence" },
  { key: "exit_load_pct", label: "Exit Load %", format: "signed_pct" },
  { key: "latest_nav", label: "Latest NAV", format: "inr" },
  { key: "latest_date", label: "NAV Date", format: "date" },
  { key: "change_1d_pct", label: "1D Chg %", format: "signed_pct" },
  { key: "return_7d_pct", label: "7D Return %", format: "signed_pct" },
  { key: "return_30d_pct", label: "30D Return %", format: "signed_pct" },
  { key: "return_90d_pct", label: "90D Return %", format: "signed_pct" },
  { key: "period_return_pct", label: "Window Return %", format: "signed_pct" },
  { key: "return_1y_pct", label: "1Y Return %", format: "signed_pct" },
  { key: "dist_from_52w_high_pct", label: "From 52W High %", format: "signed_pct" },
  { key: "isin", label: "ISIN" },
];

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
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    </label>
  );
}

export default function ScreenerPage() {
  const { start, end } = useDateRangeStore();
  const getFilter = useFilterStore((s) => s.getFilter);
  const setFilter = useFilterStore((s) => s.setFilter);

  const [amc, setAmcState] = useState(() => getFilter(SECTION, "amc", "All Fund Houses"));
  const [broadCat, setBroadCatState] = useState(() => getFilter(SECTION, "broad_cat", "All Categories"));
  const [subCat, setSubCatState] = useState(() => getFilter(SECTION, "sub_cat", "All Sub-Categories"));
  const [planType, setPlanTypeState] = useState(() => getFilter(SECTION, "plan", "All Plans"));
  const [optionType, setOptionTypeState] = useState(() => getFilter(SECTION, "option", "All Options"));
  const [terLabel, setTerLabelState] = useState(() => getFilter(SECTION, "ter_label", "All Expense Ratios"));
  const [sortLabel, setSortLabelState] = useState(() => getFilter(SECTION, "sort_label", "Selected Period Return %"));
  const [ascending, setAscendingState] = useState(() => getFilter(SECTION, "ascending", false));
  const [limit, setLimitState] = useState(() => getFilter(SECTION, "limit", 250));
  const [searchQuery, setSearchQuery] = useState("");
  const [schemeCode, setSchemeCode] = useState<number | undefined>(undefined);
  const [isolatedName, setIsolatedName] = useState<string | undefined>(undefined);
  const [plotMode, setPlotMode] = useState("Auto: Top 5 from filtered results");
  const [chartView, setChartView] = useState<"pct" | "nav">("pct");

  function setAmc(v: string) {
    setAmcState(v);
    setFilter(SECTION, "amc", v);
  }
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
  function setTerLabel(v: string) {
    setTerLabelState(v);
    setFilter(SECTION, "ter_label", v);
  }
  function setSortLabel(v: string) {
    setSortLabelState(v);
    setFilter(SECTION, "sort_label", v);
    setAscendingState(v.startsWith("Lowest"));
  }
  function setLimit(v: number) {
    setLimitState(v);
    setFilter(SECTION, "limit", v);
  }

  function resetFilters() {
    setAmc("All Fund Houses");
    setBroadCat("All Categories");
    setPlanType("All Plans");
    setOptionType("All Options");
    setTerLabel("All Expense Ratios");
    setSortLabel("Selected Period Return %");
    setLimit(250);
    setSearchQuery("");
    setSchemeCode(undefined);
    setIsolatedName(undefined);
  }

  const { data: metaFilters } = useQuery({
    queryKey: ["meta-filters", broadCat],
    queryFn: () => getMetaFilters(broadCat === "All Categories" ? undefined : broadCat),
  });

  const sortCol = SORT_OPTIONS[sortLabel] ?? "return_30d_pct";
  const maxTer = TER_OPTIONS[terLabel];

  const filterParams: ScreenerFilterParams = {
    amc: amc !== "All Fund Houses" ? amc : undefined,
    broad_cat: broadCat !== "All Categories" ? broadCat : undefined,
    sub_cat: subCat !== "All Sub-Categories" ? subCat : undefined,
    plan_type: planType !== "All Plans" ? planType : undefined,
    option_type: optionType !== "All Options" ? optionType : undefined,
    search_term: searchQuery || undefined,
    start: start || undefined,
    end: end || undefined,
    scheme_code: schemeCode,
    max_expense_ratio: maxTer,
  };

  const { data: rows, isLoading: rowsLoading } = useQuery({
    queryKey: ["screener", filterParams, sortCol, ascending, limit],
    queryFn: () => getScreener(filterParams, sortCol, ascending, limit),
  });

  const { data: kpis } = useQuery({
    queryKey: ["screener-kpis", filterParams],
    queryFn: () => getScreenerKpis(filterParams),
  });

  const span = start && end ? Math.round((new Date(end).getTime() - new Date(start).getTime()) / 86400000) : 0;
  const isolatedRow = schemeCode && rows && rows.length > 0 ? rows[0] : null;

  const totalMatching = rows?.length ?? 0;
  const plotOptions = useMemo(() => {
    const opts: string[] = [];
    if (totalMatching > 0 && totalMatching <= 15) opts.push(`Auto: All ${totalMatching} filtered schemes`);
    opts.push("Auto: Top 5 from filtered results", "Auto: Top 10 from filtered results");
    return opts;
  }, [totalMatching]);

  const plottedCodes = useMemo(() => {
    if (!rows) return [];
    if (plotMode.startsWith("Auto: All")) return rows.slice(0, 15).map((r) => r.scheme_code);
    if (plotMode === "Auto: Top 10 from filtered results") return rows.slice(0, 10).map((r) => r.scheme_code);
    return rows.slice(0, 5).map((r) => r.scheme_code);
  }, [rows, plotMode]);

  const { data: navHistory } = useQuery({
    queryKey: ["screener-chart", plottedCodes, start, end],
    queryFn: () => getNavHistory(plottedCodes, start || undefined, end || undefined),
    enabled: plottedCodes.length > 0,
  });

  const chartFigure = useMemo(() => {
    const points = navHistory ?? [];
    const byScheme = new Map<string, { x: string[]; y: number[] }>();
    const firstNav = new Map<number, number>();
    for (const p of points) {
      if (!firstNav.has(p.scheme_code)) firstNav.set(p.scheme_code, p.nav);
    }
    for (const p of points) {
      if (!byScheme.has(p.scheme_name)) byScheme.set(p.scheme_name, { x: [], y: [] });
      const entry = byScheme.get(p.scheme_name)!;
      entry.x.push(p.nav_date);
      if (chartView === "pct") {
        const base = firstNav.get(p.scheme_code) ?? p.nav;
        entry.y.push(base !== 0 ? Number((((p.nav - base) / base) * 100).toFixed(4)) : 0);
      } else {
        entry.y.push(p.nav);
      }
    }
    return {
      data: Array.from(byScheme.entries()).map(([name, { x, y }]) => ({
        type: "scatter",
        mode: "lines",
        name,
        x,
        y,
        hovertemplate: chartView === "pct" ? `${name}: <b>%{y:.4f}%</b><extra></extra>` : `${name}: <b>Rs %{y:.4f}</b><extra></extra>`,
      })),
      layout: {
        height: 450,
        hovermode: "x unified",
        yaxis: chartView === "pct" ? { ticksuffix: "%", showgrid: true } : { showgrid: true },
        legend: { orientation: "h", yanchor: "bottom", y: -0.3, xanchor: "left", x: 0 },
      },
    };
  }, [navHistory, chartView]);

  const activeFilterLabels: string[] = [];
  if (amc !== "All Fund Houses") activeFilterLabels.push(`AMC: ${amc}`);
  if (broadCat !== "All Categories") activeFilterLabels.push(`Asset Class: ${broadCat}`);
  if (subCat !== "All Sub-Categories") activeFilterLabels.push(`Category: ${subCat}`);
  if (searchQuery) activeFilterLabels.push(`Search: '${searchQuery}'`);
  if (schemeCode) activeFilterLabels.push(`Isolated scheme code: ${schemeCode}`);

  return (
    <AppShell pageContext={isolatedName ? { label: "Isolated Scheme", value: isolatedName } : undefined}>
      <h1 className="mf-page-title">Mutual Fund Scheme Screener</h1>
      <p className="mf-page-caption">Institutional screener for all mutual fund schemes using official AMFI 4-decimal data.</p>

      {/* --- KPI bar --- */}
      <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard
          title="Matching Schemes"
          value={(kpis?.total_schemes ?? 0).toLocaleString()}
          sub={schemeCode ? "Selected Single Fund" : "Based on active filters"}
        />
        <StatCard title="Latest NAV Published" value={kpis?.latest_date ? formatDate(kpis.latest_date) : "N/A"} sub="Official AMFI Published" />
        {isolatedRow ? (
          <StatCard
            title={`Period Return (${span}D)`}
            value={formatSignedPct(isolatedRow.period_return_pct as number | null)}
            sub={String(isolatedRow.scheme_name)}
            tone={toneOf(isolatedRow.period_return_pct as number | null)}
          />
        ) : kpis?.top_performer ? (
          <StatCard
            title={`Top Performer (${span}D)`}
            value={formatSignedPct(kpis.top_performer.return_pct)}
            sub={kpis.top_performer.name}
            tone={toneOf(kpis.top_performer.return_pct)}
          />
        ) : (
          <StatCard title={`Top Performer (${span}D)`} value="-" sub="No data" />
        )}
        {isolatedRow ? (
          <StatCard title="Fund Category / AMC" value={String(isolatedRow.category ?? "N/A")} sub={String(isolatedRow.fund_house ?? "")} />
        ) : kpis?.lag_performer ? (
          <StatCard
            title={`Lagging Performer (${span}D)`}
            value={formatSignedPct(kpis.lag_performer.return_pct)}
            sub={kpis.lag_performer.name}
            tone={toneOf(kpis.lag_performer.return_pct)}
            subTone="neg"
          />
        ) : (
          <StatCard title={`Lagging Performer (${span}D)`} value="-" sub="No data" />
        )}
      </div>

      {/* --- Filters --- */}
      <div className="filter-box mt-6">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm" style={{ color: "var(--mf-muted)" }}>
            Narrow down the mutual fund universe by AMC, category, plan, sort criteria, or search.
          </span>
          <button
            type="button"
            onClick={resetFilters}
            className="rounded-lg border px-3 py-1.5 text-xs font-semibold"
            style={{ borderColor: "var(--mf-border)" }}
          >
            Reset Filters
          </button>
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
          <Select label="Fund House (AMC)" value={amc} onChange={setAmc} options={["All Fund Houses", ...(metaFilters?.amcs ?? [])]} />
          <Select label="Asset Class" value={broadCat} onChange={setBroadCat} options={["All Categories", ...(metaFilters?.broad_categories ?? [])]} />
          <Select label="Category" value={subCat} onChange={setSubCat} options={["All Sub-Categories", ...(metaFilters?.sub_categories ?? [])]} />
          <Select label="Plan Type" value={planType} onChange={setPlanType} options={["All Plans", "Direct", "Regular"]} />
          <Select label="Option Type" value={optionType} onChange={setOptionType} options={["All Options", "Growth", "IDCW"]} />
          <Select label="Max TER %" value={terLabel} onChange={setTerLabel} options={Object.keys(TER_OPTIONS)} />
          <Select label="Sort By" value={sortLabel} onChange={setSortLabel} options={Object.keys(SORT_OPTIONS)} />
          <Select
            label="Sort Direction"
            value={ascending ? "Lowest to Highest (ASC)" : "Highest to Lowest (DESC)"}
            onChange={(v) => setAscendingState(v.startsWith("Lowest"))}
            options={["Highest to Lowest (DESC)", "Lowest to Highest (ASC)"]}
          />
          <Select label="Show Top" value={String(limit)} onChange={(v) => setLimit(Number(v))} options={["100", "250", "500", "1000", "2500"]} />
        </div>
        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
          <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
            Search Fund Name / AMFI Code
            <input
              type="text"
              placeholder="Type any words in any order (e.g. motilal arbitrage, 153187)..."
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                setSchemeCode(undefined);
                setIsolatedName(undefined);
              }}
              className="rounded-lg border px-2 py-1.5 text-sm"
              style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
            />
          </label>
          <div className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
            Or Isolate Specific Scheme
            <SearchCombobox
              placeholder="Search to isolate a single scheme..."
              extraParams={{
                amc: amc !== "All Fund Houses" ? amc : undefined,
                broad_cat: broadCat !== "All Categories" ? broadCat : undefined,
                sub_cat: subCat !== "All Sub-Categories" ? subCat : undefined,
                plan_type: planType !== "All Plans" ? planType : undefined,
                option_type: optionType !== "All Options" ? optionType : undefined,
              }}
              onSelect={(scheme) => {
                setSchemeCode(scheme.scheme_code);
                setIsolatedName(scheme.scheme_name);
                setSearchQuery("");
              }}
            />
            {schemeCode && (
              <button
                type="button"
                onClick={() => {
                  setSchemeCode(undefined);
                  setIsolatedName(undefined);
                }}
                className="mt-1 self-start text-xs underline"
                style={{ color: "var(--mf-accent)" }}
              >
                Clear isolated scheme
              </button>
            )}
          </div>
        </div>
      </div>

      {/* --- TER / Exit-load banner (isolated scheme only) --- */}
      {isolatedRow && (
        <div className="mt-4">
          <Banner level="info">
            <div className="flex flex-wrap items-center gap-7">
              <div>
                <div className="text-xs font-semibold uppercase" style={{ color: "var(--mf-muted)" }}>
                  Expense Ratio (TER)
                </div>
                <div className="text-lg font-bold" style={{ color: "var(--mf-accent)" }}>
                  {isolatedRow.expense_ratio !== null ? `${(isolatedRow.expense_ratio as number).toFixed(4)}% p.a.` : "Unavailable"}
                </div>
                <div className="text-xs" style={{ color: "var(--mf-muted)" }}>
                  Status: {String(isolatedRow.ter_status ?? "unknown")} · {String(isolatedRow.ter_source ?? "No source recorded")}
                </div>
              </div>
              <div className="border-l pl-5" style={{ borderColor: "var(--mf-border)" }}>
                <div className="text-xs font-semibold uppercase" style={{ color: "var(--mf-muted)" }}>
                  Exit-load data
                </div>
                <div className="text-base font-bold" style={{ color: "var(--mf-warning)" }}>
                  {String(isolatedRow.exit_rule_status ?? "unknown")}
                </div>
                <div className="text-xs" style={{ color: "var(--mf-muted)" }}>
                  {String(isolatedRow.exit_rule_source ?? "No source recorded")}
                </div>
              </div>
              <div className="flex-1 border-l pl-5" style={{ borderColor: "var(--mf-border)" }}>
                <div className="text-xs font-semibold uppercase" style={{ color: "var(--mf-muted)" }}>
                  Exit rule and lock-in
                </div>
                <div className="text-sm">
                  {String(isolatedRow.exit_load_description ?? "Exit-load rule unavailable.")} &nbsp;|&nbsp;
                  <b> {isolatedRow.lock_in_years !== null ? `Lock-in: ${isolatedRow.lock_in_years} years` : "Lock-in not verified"}</b>
                </div>
              </div>
            </div>
          </Banner>
        </div>
      )}

      {/* --- Chart --- */}
      {rows && rows.length > 0 && (
        <div className="mt-6 border-t pt-6" style={{ borderColor: "var(--mf-border)" }}>
          <h2 className="text-lg font-bold">Performance Trajectory Graph (Filtered Schemes)</h2>
          <div className="mt-2 flex flex-wrap gap-4">
            <Select label="Plot Selection" value={plotMode} onChange={setPlotMode} options={plotOptions} />
            <Select
              label="Graph Metric"
              value={chartView === "pct" ? "Normalized % Return (Base 0%)" : "Nominal NAV (Rs)"}
              onChange={(v) => setChartView(v.startsWith("Normalized") ? "pct" : "nav")}
              options={["Normalized % Return (Base 0%)", "Nominal NAV (Rs)"]}
            />
          </div>
          <div className="mt-3">
            {navHistory && navHistory.length > 0 ? (
              <PlotlyChart figure={chartFigure} />
            ) : (
              <Banner level="info">No historical NAV records found for the selected schemes in this time window.</Banner>
            )}
          </div>
        </div>
      )}

      {/* --- Table --- */}
      <div className="mt-6 border-t pt-6" style={{ borderColor: "var(--mf-border)" }}>
        <h2 className="text-lg font-bold">Filtered Performance Table</h2>
        <p className="mf-page-caption">All NAVs, returns, and percentages are computed with strict 4-decimal precision.</p>
        {!rowsLoading && rows && rows.length === 0 ? (
          <div className="mt-3">
            <Banner level="info">
              No mutual funds found matching your criteria ({activeFilterLabels.length ? activeFilterLabels.join(", ") : "no filters"}). Try
              loosening your filters.
            </Banner>
          </div>
        ) : (
          <>
            <div className="mt-3 flex items-center justify-between">
              <span className="text-sm">
                Showing <b>{(rows?.length ?? 0).toLocaleString()}</b> schemes of <b>{(kpis?.total_schemes ?? 0).toLocaleString()}</b> matching
                filters
              </span>
            </div>
            <div className="mt-2">
              <DataTable columns={SCREENER_TABLE_COLUMNS} rows={rows ?? []} keyField="scheme_code" />
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
