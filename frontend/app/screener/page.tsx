"use client";

import { useMemo, useState, Suspense } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";

import Link from "next/link";

import { AppShell } from "@/components/layout/AppShell";
import { StatCard } from "@/components/shared/StatCard";
import { DataTable, ColumnConfig } from "@/components/shared/DataTable";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { SearchCombobox } from "@/components/shared/SearchCombobox";
import { Banner } from "@/components/shared/Banner";
import { formatSignedPct, formatDate, toneOf } from "@/lib/format";
import { useDebouncedValue, useUrlSync } from "@/lib/hooks";
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
  {
    key: "scheme_name",
    label: "Scheme Name",
    render: (row) => (
      <Link href={`/scheme/${row.scheme_code}`} className="font-semibold" style={{ color: "var(--mf-accent)" }}>
        {String(row.scheme_name ?? "")}
      </Link>
    ),
  },
  { key: "fund_house", label: "AMC" },
  { key: "category", label: "Category" },
  { key: "plan_type", label: "Plan" },
  { key: "option_type", label: "Option" },
  {
    key: "expense_ratio",
    label: "TER %",
    format: "number",
    sortValue: (row) => (row.expense_ratio != null ? Number(row.expense_ratio) : row.ter_base_expense_ratio != null ? Number(row.ter_base_expense_ratio) : null),
    render: (row) => {
      const baseTer = row.ter_base_expense_ratio != null ? Number(row.ter_base_expense_ratio) : null;
      const totalTer = row.expense_ratio != null ? Number(row.expense_ratio) : null;
      if (baseTer !== null) {
        return (
          <div className="flex flex-col" title={`Base TER: ${baseTer.toFixed(4)}% | Total TER: ${totalTer !== null ? totalTer.toFixed(4) : "N/A"}%`}>
            <span className="font-semibold text-xs" style={{ color: "var(--mf-fg)" }}>
              {baseTer.toFixed(4)}%
            </span>
            {totalTer !== null && Math.abs(totalTer - baseTer) > 0.001 && (
              <span className="text-[10px]" style={{ color: "var(--mf-muted)" }}>
                Total: {totalTer.toFixed(2)}%
              </span>
            )}
          </div>
        );
      }
      return totalTer !== null ? `${totalTer.toFixed(4)}%` : "-";
    },
  },
  { key: "ter_status", label: "TER Confidence" },
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
  return (
    <Suspense fallback={<div className="p-6 text-sm">Loading Mutual Fund Screener...</div>}>
      <ScreenerContent />
    </Suspense>
  );
}

function ScreenerContent() {
  const searchParams = useSearchParams();
  const { start, end, planType, optionType, setPlanType, setOptionType } = useDateRangeStore();
  const getFilter = useFilterStore((s) => s.getFilter);
  const setFilter = useFilterStore((s) => s.setFilter);

  const [amc, setAmcState] = useState(() => searchParams.get("amc") || getFilter(SECTION, "amc", "All Fund Houses"));
  const [broadCat, setBroadCatState] = useState(() => searchParams.get("broad_cat") || getFilter(SECTION, "broad_cat", "All Categories"));
  const [subCat, setSubCatState] = useState(() => searchParams.get("sub_cat") || getFilter(SECTION, "sub_cat", "All Sub-Categories"));
  const [terLabel, setTerLabelState] = useState(() => searchParams.get("ter_label") || getFilter(SECTION, "ter_label", "All Expense Ratios"));
  const [officialTerOnly, setOfficialTerOnly] = useState(() => searchParams.get("official_ter") === "1");
  const [sortLabel, setSortLabelState] = useState(() => searchParams.get("sort") || getFilter(SECTION, "sort_label", "Selected Period Return %"));
  const [ascending, setAscendingState] = useState(() => getFilter(SECTION, "ascending", false));
  const [limit, setLimitState] = useState(() => getFilter(SECTION, "limit", 250));
  const [searchQuery, setSearchQuery] = useState(() => searchParams.get("search") || "");
  const [schemeCode, setSchemeCode] = useState<number | undefined>(() =>
    searchParams.get("scheme_code") ? Number(searchParams.get("scheme_code")) : undefined
  );
  const [isolatedName, setIsolatedName] = useState<string | undefined>(undefined);
  const [plotMode, setPlotMode] = useState("Auto: Top 5 from filtered results");
  const [chartView, setChartView] = useState<"pct" | "nav">("pct");

  // Sync state with URL search parameters
  useUrlSync({
    amc: amc !== "All Fund Houses" ? amc : undefined,
    broad_cat: broadCat !== "All Categories" ? broadCat : undefined,
    sub_cat: subCat !== "All Sub-Categories" ? subCat : undefined,
    ter_label: terLabel !== "All Expense Ratios" ? terLabel : undefined,
    official_ter: officialTerOnly ? "1" : undefined,
    sort: sortLabel !== "Selected Period Return %" ? sortLabel : undefined,
    search: searchQuery || undefined,
    scheme_code: schemeCode || undefined,
  });

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
  const debouncedSearch = useDebouncedValue(searchQuery, 400);

  const filterParams: ScreenerFilterParams = {
    amc: amc !== "All Fund Houses" ? amc : undefined,
    broad_cat: broadCat !== "All Categories" ? broadCat : undefined,
    sub_cat: subCat !== "All Sub-Categories" ? subCat : undefined,
    plan_type: planType !== "All Plans" ? planType : undefined,
    option_type: optionType !== "All Options" ? optionType : undefined,
    search_term: (schemeCode ? undefined : debouncedSearch) || undefined,
    start: start || undefined,
    end: end || undefined,
    scheme_code: schemeCode,
    max_expense_ratio: maxTer,
    official_ter_only: officialTerOnly || undefined,
  };

  const { data: rows, isLoading: rowsLoading } = useQuery({
    queryKey: ["screener", filterParams, sortCol, ascending, limit],
    queryFn: () => getScreener(filterParams, sortCol, ascending, limit),
    placeholderData: keepPreviousData,
  });

  const { data: kpis } = useQuery({
    queryKey: ["screener-kpis", filterParams],
    queryFn: () => getScreenerKpis(filterParams),
    placeholderData: keepPreviousData,
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
    placeholderData: keepPreviousData,
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
        hoversort: "value descending",
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

      {/* --- Chart --- */}
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
        <div className="mt-3 min-h-[460px]">
          {rows && rows.length > 0 ? (
            navHistory && navHistory.length > 0 ? (
              <PlotlyChart figure={chartFigure} />
            ) : (
              <div className="flex h-[450px] items-center justify-center rounded-lg border text-sm" style={{ borderColor: "var(--mf-border)", color: "var(--mf-muted)" }}>
                <Banner level="info">No historical NAV records found for the selected schemes in this time window.</Banner>
              </div>
            )
          ) : rowsLoading ? (
            <div className="flex h-[450px] items-center justify-center rounded-lg border text-sm" style={{ borderColor: "var(--mf-border)", color: "var(--mf-muted)" }}>
              Updating chart...
            </div>
          ) : (
            <div className="flex h-[450px] items-center justify-center rounded-lg border text-sm" style={{ borderColor: "var(--mf-border)", color: "var(--mf-muted)" }}>
              <Banner level="info">No funds match the current filter selection to plot.</Banner>
            </div>
          )}
        </div>
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
          <Select label="Plan Type (Global)" value={planType} onChange={(v) => setPlanType(v as any)} options={["All Plans", "Direct", "Regular"]} />
          <Select label="Option Type (Global)" value={optionType} onChange={(v) => setOptionType(v as any)} options={["All Options", "Growth", "IDCW"]} />
          <Select label="Max TER %" value={terLabel} onChange={setTerLabel} options={Object.keys(TER_OPTIONS)} />
          <label className="flex items-end gap-2 text-xs font-medium pb-2" style={{ color: "var(--mf-muted)" }}>
            <input type="checkbox" checked={officialTerOnly} onChange={(e) => setOfficialTerOnly(e.target.checked)} />
            Official TER only
          </label>
          <Select label="Sort By" value={sortLabel} onChange={setSortLabel} options={Object.keys(SORT_OPTIONS)} />
          <Select
            label="Sort Direction"
            value={ascending ? "Lowest to Highest (ASC)" : "Highest to Lowest (DESC)"}
            onChange={(v) => setAscendingState(v.startsWith("Lowest"))}
            options={["Highest to Lowest (DESC)", "Lowest to Highest (ASC)"]}
          />
          <Select label="Show Top" value={String(limit)} onChange={(v) => setLimit(Number(v))} options={["100", "250", "500", "1000", "2500"]} />
        </div>
        <div className="mt-3">
          <div className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
            <span>Search Fund Name / AMFI Code (Live suggestions in dropdown)</span>
            <SearchCombobox
              placeholder="Type any words in any order (e.g. motilal arbitrage, small cap, 153187)..."
              value={searchQuery}
              onChangeQuery={(q) => {
                setSearchQuery(q);
                if (!q) {
                  setSchemeCode(undefined);
                  setIsolatedName(undefined);
                }
              }}
              onClear={() => {
                setSearchQuery("");
                setSchemeCode(undefined);
                setIsolatedName(undefined);
              }}
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
                setSearchQuery(scheme.scheme_name);
              }}
            />
            {schemeCode && isolatedName && (
              <div className="mt-1.5 flex items-center gap-2">
                <span className="text-xs font-semibold" style={{ color: "var(--mf-accent)" }}>
                  Isolating fund: {isolatedName} [{schemeCode}]
                </span>
                <button
                  type="button"
                  onClick={() => {
                    setSchemeCode(undefined);
                    setIsolatedName(undefined);
                    setSearchQuery("");
                  }}
                  className="rounded border px-2 py-0.5 text-xs font-semibold"
                  style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}
                >
                  Clear isolation
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* --- TER banner (isolated scheme only) --- */}
      {isolatedRow && (
        <div className="mt-4">
          <Banner level="info">
            <div className="flex flex-wrap items-center gap-6">
              <div>
                <div className="text-xs font-semibold uppercase" style={{ color: "var(--mf-muted)" }}>
                  Base Expense Ratio (AMC)
                </div>
                <div className="text-xl font-bold" style={{ color: "var(--mf-accent)" }}>
                  {isolatedRow.ter_base_expense_ratio !== null && isolatedRow.ter_base_expense_ratio !== undefined
                    ? `${Number(isolatedRow.ter_base_expense_ratio).toFixed(4)}% p.a.`
                    : isolatedRow.expense_ratio !== null
                    ? `${Number(isolatedRow.expense_ratio).toFixed(4)}% p.a.`
                    : "Unavailable"}
                </div>
                <div className="text-xs" style={{ color: "var(--mf-muted)" }}>
                  Gross Total TER: {isolatedRow.expense_ratio !== null ? `${Number(isolatedRow.expense_ratio).toFixed(4)}% p.a.` : "Unavailable"}
                </div>
              </div>
              <div className="border-l pl-5" style={{ borderColor: "var(--mf-border)" }}>
                <div className="text-xs font-semibold uppercase" style={{ color: "var(--mf-muted)" }}>
                  As-of date
                </div>
                <div className="text-sm font-semibold">{isolatedRow.ter_as_of_date ? String(isolatedRow.ter_as_of_date) : "Not dated"}</div>
                <div className="text-xs" style={{ color: "var(--mf-muted)" }}>
                  Status: {String(isolatedRow.ter_status ?? "unknown")}
                </div>
              </div>
              {isolatedRow.ter_base_expense_ratio !== null && isolatedRow.ter_base_expense_ratio !== undefined && (
                <div className="border-l pl-5 text-xs" style={{ borderColor: "var(--mf-border)", color: "var(--mf-muted)" }}>
                  <div className="font-semibold uppercase tracking-wider" style={{ color: "var(--mf-fg)" }}>SEBI Reg 66 Breakdown</div>
                  <div>Base Fee: <span className="font-semibold" style={{ color: "var(--mf-fg)" }}>{Number(isolatedRow.ter_base_expense_ratio).toFixed(4)}%</span></div>
                  <div>Statutory GST: <span className="font-semibold" style={{ color: "var(--mf-fg)" }}>{Number(isolatedRow.ter_statutory_levies_pct ?? 0).toFixed(4)}%</span></div>
                  <div>Brokerage &amp; Trans: <span className="font-semibold" style={{ color: "var(--mf-fg)" }}>{(Number(isolatedRow.ter_brokerage_cost_pct ?? 0) + Number(isolatedRow.ter_transaction_cost_pct ?? 0)).toFixed(4)}%</span></div>
                  <div className="mt-1 pt-1 border-t" style={{ borderColor: "var(--mf-border)" }}>
                    Total Gross TER: <span className="font-bold" style={{ color: "var(--mf-accent)" }}>{isolatedRow.expense_ratio !== null ? `${Number(isolatedRow.expense_ratio).toFixed(4)}%` : "N/A"}</span>
                  </div>
                </div>
              )}
            </div>
          </Banner>
        </div>
      )}

      {/* --- Table --- */}
      <div className="mt-6 border-t pt-6 min-h-[450px]" style={{ borderColor: "var(--mf-border)" }}>
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
