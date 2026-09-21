"use client";

import { useMemo, useState, Suspense } from "react";
import { useQuery, useQueries, keepPreviousData } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";

import { AppShell } from "@/components/layout/AppShell";
import { StatCard } from "@/components/shared/StatCard";
import { DataTable, ColumnConfig } from "@/components/shared/DataTable";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { SearchCombobox } from "@/components/shared/SearchCombobox";
import { Banner } from "@/components/shared/Banner";
import { NonAdviceDisclaimer } from "@/components/shared/Disclaimer";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { formatSignedPct, formatInr, toneOf, CHART_MUTED_COLOR } from "@/lib/format";
import { useUrlSync } from "@/lib/hooks";
import { useDateRangeStore } from "@/lib/stores/dateRange";
import { useFilterStore } from "@/lib/stores/filters";
import { getNavHistory, getScheme, type NavHistoryPoint } from "@/lib/api/schemes";
import { runBacktest, type BacktestResult } from "@/lib/api/backtest";
import { portfolio_sim_REBALANCE } from "@/lib/rebalance";

const SECTION = "compare_simulate";
const DEFAULT_SCHEME_CODES = [122639, 118989];
const MAX_SCHEMES = 6;

function estimateTerDrag(investmentAmount: number, finalValue: number, holdingDays: number, terPct: number | null): number | null {
  if (terPct === null || investmentAmount < 0 || finalValue < 0 || holdingDays < 0) return null;
  const years = holdingDays / 365.25;
  return ((investmentAmount + finalValue) / 2.0) * (terPct / 100.0) * years;
}

const COMPARE_TABLE_COLUMNS: ColumnConfig[] = [
  { key: "scheme_name", label: "Scheme Name" },
  { key: "fund_house", label: "Fund House" },
  { key: "category", label: "Category" },
  { key: "plan_type", label: "Plan" },
  { key: "expense_ratio", label: "TER %", format: "signed_pct" },
  { key: "ter_status", label: "TER Confidence" },
  { key: "latest_nav", label: "Latest NAV", format: "inr" },
  { key: "window_return_pct", label: "Window Return %", format: "signed_pct" },
  { key: "return_30d_pct", label: "30D Return %", format: "signed_pct" },
  { key: "return_1y_pct", label: "1Y Return %", format: "signed_pct" },
  { key: "dist_from_52w_high_pct", label: "From 52W High %", format: "signed_pct" },
  { key: "isin", label: "ISIN" },
];

export default function ComparePage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm">Loading Compare & Backtest...</div>}>
      <CompareContent />
    </Suspense>
  );
}

function CompareContent() {
  const searchParams = useSearchParams();
  const { start, end, planType, optionType } = useDateRangeStore();
  const getFilter = useFilterStore((s) => s.getFilter);
  const setFilter = useFilterStore((s) => s.setFilter);

  const rawCodes = searchParams.get("codes");
  const initialCodes: number[] = rawCodes
    ? rawCodes
        .split(",")
        .map(Number)
        .filter((c) => !Number.isNaN(c) && c > 0)
    : getFilter(SECTION, "selected_scheme_codes", DEFAULT_SCHEME_CODES);

  const paramTab = searchParams.get("tab");
  const initialTab: "compare" | "backtest" = paramTab === "backtest" ? "backtest" : "compare";

  const paramAmount = searchParams.get("amount");
  const initialAmount = paramAmount && !Number.isNaN(Number(paramAmount))
    ? Number(paramAmount)
    : getFilter(SECTION, "investment_amount", 10000);

  const [selectedCodes, setSelectedCodesState] = useState<number[]>(() => initialCodes.length > 0 ? initialCodes : DEFAULT_SCHEME_CODES);
  const [investmentAmount, setInvestmentAmountState] = useState<number>(() => initialAmount);
  const [activeTab, setActiveTab] = useState<"compare" | "backtest">(() => initialTab);
  const [chartTab, setChartTab] = useState<"growth" | "normalized" | "nav">("growth");
  const [backtestChartTab, setBacktestChartTab] = useState<"growth" | "alloc" | "breakdown">("growth");

  function setSelectedCodes(codes: number[]) {
    setSelectedCodesState(codes);
    setFilter(SECTION, "selected_scheme_codes", codes);
  }
  function setInvestmentAmount(v: number) {
    setInvestmentAmountState(v);
    setFilter(SECTION, "investment_amount", v);
  }
  function addScheme(code: number) {
    if (selectedCodes.includes(code) || selectedCodes.length >= MAX_SCHEMES) return;
    setSelectedCodes([...selectedCodes, code]);
  }
  function removeScheme(code: number) {
    setSelectedCodes(selectedCodes.filter((c) => c !== code));
  }
  function resetSelection() {
    setSelectedCodes(DEFAULT_SCHEME_CODES);
    setInvestmentAmount(10000);
  }

  const profileQueries = useQueries({
    queries: selectedCodes.map((code) => ({
      queryKey: ["scheme", code],
      queryFn: () => getScheme(code),
    })),
  });
  const profiles = profileQueries.map((q) => q.data?.profile).filter((p): p is NonNullable<typeof p> => !!p);
  const schemeNames = new Map(profiles.map((p) => [p.scheme_code, p.scheme_name]));

  const { data: navHistory } = useQuery({
    queryKey: ["compare-nav", selectedCodes, start, end],
    queryFn: () => getNavHistory(selectedCodes, start || undefined, end || undefined),
    enabled: selectedCodes.length > 0 && !!start && !!end,
    placeholderData: keepPreviousData,
  });

  const span = start && end ? Math.round((new Date(end).getTime() - new Date(start).getTime()) / 86400000) : 0;

  // --- Tab 1: window returns per scheme (first vs last NAV in the fetched window) ---
  const windowReturns = useMemo(() => {
    const out = new Map<number, { pct: number; days: number }>();
    if (!navHistory) return out;
    const byScheme = new Map<number, NavHistoryPoint[]>();
    for (const p of navHistory) {
      if (!byScheme.has(p.scheme_code)) byScheme.set(p.scheme_code, []);
      byScheme.get(p.scheme_code)!.push(p);
    }
    for (const [code, points] of byScheme) {
      if (points.length === 0) continue;
      const first = points[0];
      const last = points[points.length - 1];
      const days = Math.round((new Date(last.nav_date).getTime() - new Date(first.nav_date).getTime()) / 86400000);
      out.set(code, { pct: Number((((last.nav - first.nav) / first.nav) * 100).toFixed(4)), days });
    }
    return out;
  }, [navHistory]);

  const compareRows = profiles.map((p) => ({
    ...p,
    window_return_pct: windowReturns.get(p.scheme_code)?.pct ?? null,
  }));

  const growthFigure = useMemo(() => {
    const points = navHistory ?? [];
    const byScheme = new Map<string, { x: string[]; y: number[]; firstNav: number | null }>();
    for (const p of points) {
      if (!byScheme.has(p.scheme_name)) byScheme.set(p.scheme_name, { x: [], y: [], firstNav: null });
      const entry = byScheme.get(p.scheme_name)!;
      if (entry.firstNav === null) entry.firstNav = p.nav;
      entry.x.push(p.nav_date);
      if (chartTab === "growth") {
        entry.y.push(Number((investmentAmount * (p.nav / entry.firstNav!)).toFixed(2)));
      } else if (chartTab === "normalized") {
        entry.y.push(Number((((p.nav - entry.firstNav!) / entry.firstNav!) * 100).toFixed(4)));
      } else {
        entry.y.push(p.nav);
      }
    }
    return {
      data: Array.from(byScheme.entries()).map(([name, { x, y }]) => ({
        type: "scatter",
        mode: "lines+markers",
        name,
        x,
        y,
        hovertemplate:
          chartTab === "growth" ? `${name}: <b>Rs %{y:,.2f}</b><extra></extra>` : chartTab === "normalized" ? `${name}: <b>%{y:.4f}%</b><extra></extra>` : `${name}: <b>Rs %{y:.4f}</b><extra></extra>`,
      })),
      layout: {
        height: 460,
        hovermode: "x unified",
        hoversort: "value descending",
        yaxis: chartTab === "normalized" ? { ticksuffix: "%", showgrid: true } : { showgrid: true },
        legend: { orientation: "h", yanchor: "bottom", y: -0.3, xanchor: "left", x: 0 },
      },
    };
  }, [navHistory, chartTab, investmentAmount]);

  const costRows = profiles.map((p) => {
    const win = windowReturns.get(p.scheme_code);
    const terVal = p.expense_ratio as number | null;
    const nominalValue = win ? investmentAmount * (1 + win.pct / 100) : investmentAmount;
    const terEstimate = p.ter_status === "official" && win ? estimateTerDrag(investmentAmount, nominalValue, win.days, terVal) : null;
    return {
      scheme_name: p.scheme_name,
      plan_type: p.plan_type,
      expense_ratio: terVal,
      ter_status: p.ter_status,
      nominal_value: nominalValue,
      ter_estimate: terEstimate,
      window_return_pct: win?.pct ?? null,
    };
  });

  // --- Tab 2: Portfolio Backtest ---
  const paramWeights = searchParams.get("weights");
  const parsedWeights: Record<number, number> = {};
  if (paramWeights) {
    paramWeights.split(",").forEach((pair) => {
      const [k, v] = pair.split(":");
      const code = Number(k);
      const val = Number(v);
      if (!Number.isNaN(code) && !Number.isNaN(val)) {
        parsedWeights[code] = val;
      }
    });
  }

  const [weights, setWeightsState] = useState<Record<number, number>>(() => {
    const w: Record<number, number> = {};
    for (const c of selectedCodes) {
      if (parsedWeights[c] !== undefined) {
        w[c] = parsedWeights[c];
      } else {
        w[c] = getFilter("portfolio_weights", String(c), Number((100 / selectedCodes.length).toFixed(2)));
      }
    }
    return w;
  });
  const paramMode = searchParams.get("mode") as "Lump Sum" | "SIP (Monthly)" | null;
  const paramRebal = searchParams.get("rebalance");
  const paramLumpSum = searchParams.get("lump_sum");
  const paramSipAmount = searchParams.get("sip_amount");

  const [invMode, setInvModeState] = useState<"Lump Sum" | "SIP (Monthly)">(
    () => (paramMode === "Lump Sum" || paramMode === "SIP (Monthly)" ? paramMode : getFilter("portfolio_config", "mode", "Lump Sum"))
  );
  const [lumpSum, setLumpSumState] = useState<number>(() =>
    paramLumpSum && !Number.isNaN(Number(paramLumpSum))
      ? Number(paramLumpSum)
      : getFilter("portfolio_config", "lump_sum_amount", 100000)
  );
  const [sipAmount, setSipAmountState] = useState<number>(() =>
    paramSipAmount && !Number.isNaN(Number(paramSipAmount))
      ? Number(paramSipAmount)
      : getFilter("portfolio_config", "sip_amount", 5000)
  );
  const [rebalanceFreq, setRebalanceFreqState] = useState<string>(
    () => (paramRebal ? paramRebal : getFilter("portfolio_config", "rebalance", "None"))
  );

  const serializedWeights = useMemo(() => {
    const keys = Object.keys(weights);
    if (keys.length === 0) return undefined;
    return Object.entries(weights).map(([k, v]) => `${k}:${v}`).join(",");
  }, [weights]);

  // Synchronize state with URL query parameters for direct link sharing & bookmarking
  useUrlSync({
    codes: selectedCodes.length > 0 ? selectedCodes.join(",") : undefined,
    tab: activeTab !== "compare" ? activeTab : undefined,
    amount: investmentAmount !== 10000 ? investmentAmount : undefined,
    mode: invMode !== "Lump Sum" ? invMode : undefined,
    rebalance: rebalanceFreq !== "None" ? rebalanceFreq : undefined,
    lump_sum: invMode === "Lump Sum" && lumpSum !== 100000 ? lumpSum : undefined,
    sip_amount: invMode === "SIP (Monthly)" && sipAmount !== 5000 ? sipAmount : undefined,
    weights: serializedWeights,
  });

  function setWeight(code: number, v: number) {
    const next = { ...weights, [code]: v };
    setWeightsState(next);
    setFilter("portfolio_weights", String(code), v);
  }
  function setInvMode(v: "Lump Sum" | "SIP (Monthly)") {
    setInvModeState(v);
    setFilter("portfolio_config", "mode", v);
  }
  function setLumpSum(v: number) {
    setLumpSumState(v);
    setFilter("portfolio_config", "lump_sum_amount", v);
  }
  function setSipAmount(v: number) {
    setSipAmountState(v);
    setFilter("portfolio_config", "sip_amount", v);
  }
  function setRebalanceFreq(v: string) {
    setRebalanceFreqState(v);
    setFilter("portfolio_config", "rebalance", v);
  }

  const weightSum = selectedCodes.reduce((s, c) => s + (weights[c] ?? 0), 0);

  const { data: backtestResult, isFetching: backtestLoading } = useQuery({
    queryKey: ["backtest", selectedCodes, weights, invMode, lumpSum, sipAmount, rebalanceFreq, start, end],
    queryFn: () =>
      runBacktest({
        scheme_codes: selectedCodes,
        weights: Object.fromEntries(selectedCodes.map((c) => [c, weights[c] ?? 0])),
        mode: invMode,
        lump_sum_amount: invMode === "Lump Sum" ? lumpSum : 0,
        sip_amount: invMode === "SIP (Monthly)" ? sipAmount : 0,
        rebalance_freq: rebalanceFreq as never,
        start_date: start,
        end_date: end,
      }),
    enabled: activeTab === "backtest" && selectedCodes.length > 0 && weightSum > 0 && !!start && !!end,
  });

  const btResultOk: BacktestResult | undefined = backtestResult && !backtestResult.error ? backtestResult : undefined;

  const btGrowthFigure = useMemo(() => {
    const rows = btResultOk?.df_result ?? [];
    return {
      data: [
        {
          type: "scatter",
          mode: "lines",
          name: "Portfolio Value",
          x: rows.map((r) => r.nav_date),
          y: rows.map((r) => r.portfolio_value),
          line: { color: "#2563EB", width: 2.5 },
        },
        {
          type: "scatter",
          mode: "lines",
          name: "Capital Invested",
          x: rows.map((r) => r.nav_date),
          y: rows.map((r) => r.total_invested),
          line: { color: CHART_MUTED_COLOR, width: 1.5, dash: "dash" },
        },
      ],
      layout: { height: 460, hovermode: "x unified", hoversort: "value descending", yaxis: { showgrid: true }, legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "right", x: 1 } },
    };
  }, [btResultOk]);

  const btAllocFigure = useMemo(() => {
    const rows = btResultOk?.df_result ?? [];
    const codes = selectedCodes;
    const traces = codes.map((c) => {
      const y = rows.map((r) => {
        const total = codes.reduce((s, cc) => s + ((r[`holding_${cc}`] as number) ?? 0), 0) || 1;
        return (((r[`holding_${c}`] as number) ?? 0) / total) * 100;
      });
      return { type: "scatter", mode: "lines", stackgroup: "one", name: schemeNames.get(c) ?? String(c), x: rows.map((r) => r.nav_date), y };
    });
    return {
      data: traces,
      layout: { height: 440, hovermode: "x unified", hoversort: "value descending", yaxis: { ticksuffix: "%", range: [0, 100] }, legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "right", x: 1 } },
    };
  }, [btResultOk, selectedCodes, schemeNames]);

  const btBreakdownRows = useMemo(() => {
    const rows = btResultOk?.df_result ?? [];
    if (rows.length === 0) return [];
    const final = rows[rows.length - 1];
    const codes = selectedCodes;
    const finalTotal = codes.reduce((s, c) => s + ((final[`holding_${c}`] as number) ?? 0), 0) || 1;
    const nw = btResultOk?.normalized_weights ?? {};
    return codes.map((c) => ({
      fund: schemeNames.get(c) ?? String(c),
      target_weight_pct: Number(((nw[c] ?? 0) * 100).toFixed(2)),
      actual_weight_pct: Number((((final[`holding_${c}`] as number) ?? 0) / finalTotal * 100).toFixed(2)),
      current_value: Number(((final[`holding_${c}`] as number) ?? 0).toFixed(2)),
    }));
  }, [btResultOk, selectedCodes, schemeNames]);

  return (
    <AppShell pageContext={selectedCodes.length ? { label: "Selected Funds", value: String(selectedCodes.length) } : undefined}>
      <h1 className="mf-page-title">Compare & Simulate</h1>
      <div className="mt-3">
        <NonAdviceDisclaimer compact />
      </div>
      <p className="mf-page-caption">Compare mutual fund schemes side-by-side, then backtest the same funds together as a weighted portfolio.</p>

      {/* --- Shared fund selector --- */}
      <div className="filter-box mt-4">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm" style={{ color: "var(--mf-muted)" }}>
            Select up to {MAX_SCHEMES} funds -- shared by both tabs below.
            <span className="ml-2 inline-flex items-center gap-1.5 text-xs font-semibold" style={{ color: "var(--mf-accent)" }}>
              • Universe: {planType} / {optionType}
            </span>
          </span>
          <button type="button" onClick={resetSelection} className="rounded-lg border px-3 py-1.5 text-xs font-semibold hover:bg-slate-50 transition-colors" style={{ borderColor: "var(--mf-border)", color: "var(--mf-fg)" }}>
            Reset Selection
          </button>
        </div>
        <SearchCombobox
          placeholder="Search & add a fund (filtered by header Plan/Option)..."
          extraParams={{
            plan_type: planType !== "All Plans" ? planType : undefined,
            option_type: optionType !== "All Options" ? optionType : undefined,
          }}
          onSelect={(s) => addScheme(s.scheme_code)}
        />
        <div className="mt-2 flex flex-wrap gap-2">
          {selectedCodes.map((code) => (
            <span key={code} className="mf-pill mf-pill-neutral">
              {schemeNames.get(code) ?? code}
              <button type="button" onClick={() => removeScheme(code)} className="ml-2 font-bold">
                ×
              </button>
            </span>
          ))}
        </div>
      </div>

      {selectedCodes.length === 0 ? (
        <div className="mt-6">
          <Banner level="info">Please select at least 1 fund from the dropdown above to compare or simulate.</Banner>
        </div>
      ) : !navHistory ? null : navHistory.length === 0 ? (
        <div className="mt-6">
          <Banner level="warning">No historical NAV records for any selected fund in this time window. Widen the time horizon, or pick different funds.</Banner>
        </div>
      ) : (
        <>
          <div className="mt-6 flex gap-1.5 border-b pb-2" style={{ borderColor: "var(--mf-border)" }}>
            <button
              type="button"
              onClick={() => setActiveTab("compare")}
              className="rounded-full px-3 py-1.5 text-xs font-semibold"
              style={{ background: activeTab === "compare" ? "var(--mf-accent-bg)" : "transparent", color: activeTab === "compare" ? "var(--mf-accent)" : "var(--mf-fg)" }}
            >
              Head-to-Head Comparison
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("backtest")}
              className="rounded-full px-3 py-1.5 text-xs font-semibold"
              style={{ background: activeTab === "backtest" ? "var(--mf-accent-bg)" : "transparent", color: activeTab === "backtest" ? "var(--mf-accent)" : "var(--mf-fg)" }}
            >
              Portfolio Backtest
            </button>
          </div>

          {activeTab === "compare" && (
            <div className="mt-4">
              <label className="flex max-w-sm flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
                Hypothetical Investment (Rs) -- used for the growth chart & cost illustration below
                <input
                  type="number"
                  min={1000}
                  max={10000000}
                  step={5000}
                  value={investmentAmount}
                  onChange={(e) => setInvestmentAmount(Number(e.target.value))}
                  className="rounded-lg border px-2 py-1.5 text-sm"
                  style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
                />
              </label>

              <h2 className="mt-4 text-lg font-bold">Side-by-Side Metrics Comparison</h2>
              <div className="mt-2">
                <DataTable columns={COMPARE_TABLE_COLUMNS} rows={compareRows} keyField="scheme_code" />
              </div>

              <h2 className="mt-6 text-lg font-bold border-t pt-4" style={{ borderColor: "var(--mf-border)" }}>
                Visual Performance Comparison
              </h2>
              <div className="mt-2 flex gap-1.5">
                {(["growth", "normalized", "nav"] as const).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setChartTab(t)}
                    className="rounded-full px-3 py-1.5 text-xs font-semibold"
                    style={{ background: chartTab === t ? "var(--mf-accent-bg)" : "transparent", color: chartTab === t ? "var(--mf-accent)" : "var(--mf-fg)" }}
                  >
                    {t === "growth" ? `Growth of ${formatInr(investmentAmount)}` : t === "normalized" ? "Normalized % Return" : "Nominal NAV"}
                  </button>
                ))}
              </div>
              <div className="mt-3">
                <PlotlyChart figure={growthFigure} />
              </div>

              <h2 className="mt-6 text-lg font-bold border-t pt-4" style={{ borderColor: "var(--mf-border)" }}>
                Cost Data (TER)
              </h2>
              <p className="mf-page-caption">NAV returns are net of TER. This page does not infer a redemption payout from the chart period.</p>
              <div className="mt-2">
                <DataTable
                  columns={[
                    { key: "scheme_name", label: "Scheme Name" },
                    { key: "plan_type", label: "Plan" },
                    { key: "expense_ratio", label: "TER %", format: "signed_pct" },
                    { key: "ter_status", label: "TER Confidence" },
                    { key: "nominal_value", label: "NAV Value (not redemption payout)", format: "inr" },
                    { key: "ter_estimate", label: "Current-TER Illustration", format: "inr" },
                    { key: "window_return_pct", label: "NAV Return %", format: "signed_pct" },
                  ]}
                  rows={costRows}
                  keyField="scheme_name"
                />
              </div>
            </div>
          )}

          {activeTab === "backtest" && (
            <div className="mt-4">
              <Banner level="info">
                This is a historical backtest of official AMFI NAVs over the time horizon selected above -- not a forward-looking projection.
                Rebalancing is simulated frictionlessly (no transaction costs, exit loads, or capital-gains tax).
              </Banner>

              <div className="mt-4">
                <div className="text-sm font-semibold" style={{ color: "var(--mf-fg)" }}>Target Allocation Weights (auto-normalized to 100%)</div>
                <div className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
                  {selectedCodes.map((code) => (
                    <label key={code} className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }} title={schemeNames.get(code)}>
                      {(schemeNames.get(code) ?? String(code)).slice(0, 24)}
                      <input
                        type="number"
                        min={0}
                        max={100}
                        step={1}
                        value={weights[code] ?? 0}
                        onChange={(e) => setWeight(code, Number(e.target.value))}
                        className="rounded-lg border px-2 py-1.5 text-sm"
                        style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
                      />
                    </label>
                  ))}
                </div>
                {weightSum <= 0 ? (
                  <p className="mt-1 text-xs" style={{ color: "var(--mf-danger)" }}>
                    At least one fund needs a positive weight.
                  </p>
                ) : Math.abs(weightSum - 100) > 0.5 ? (
                  <p className="mt-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
                    Weights sum to <b style={{ color: "var(--mf-fg)" }}>{weightSum.toFixed(1)}%</b> -- normalized proportionally to 100% for the simulation.
                  </p>
                ) : null}
              </div>

              <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
                <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
                  Investment Mode
                  <select
                    value={invMode}
                    onChange={(e) => setInvMode(e.target.value as never)}
                    className="rounded-lg border px-2 py-1.5 text-sm"
                    style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
                  >
                    <option className="bg-white text-slate-900">Lump Sum</option>
                    <option className="bg-white text-slate-900">SIP (Monthly)</option>
                  </select>
                </label>
                {invMode === "Lump Sum" ? (
                  <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
                    Lump Sum Amount (Rs)
                    <input
                      type="number"
                      min={1000}
                      step={5000}
                      value={lumpSum}
                      onChange={(e) => setLumpSum(Number(e.target.value))}
                      className="rounded-lg border px-2 py-1.5 text-sm"
                      style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
                    />
                  </label>
                ) : (
                  <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
                    Monthly SIP Amount (Rs)
                    <input
                      type="number"
                      min={500}
                      step={500}
                      value={sipAmount}
                      onChange={(e) => setSipAmount(Number(e.target.value))}
                      className="rounded-lg border px-2 py-1.5 text-sm"
                      style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
                    />
                  </label>
                )}
                <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
                  Rebalancing Frequency
                  <select
                    value={rebalanceFreq}
                    onChange={(e) => setRebalanceFreq(e.target.value)}
                    className="rounded-lg border px-2 py-1.5 text-sm"
                    style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
                  >
                    {portfolio_sim_REBALANCE.map((r) => (
                      <option key={r} value={r} className="bg-white text-slate-900">
                        {r}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              {backtestLoading ? (
                <p className="mt-6 text-sm" style={{ color: "var(--mf-muted)" }}>
                  Running backtest...
                </p>
              ) : backtestResult?.error ? (
                <div className="mt-6">
                  <Banner level="warning">{backtestResult.error}</Banner>
                </div>
              ) : btResultOk ? (
                <>
                  <div className="mt-6 grid grid-cols-2 gap-4 md:grid-cols-4">
                    <StatCard title="Final Portfolio Value" value={formatInr(btResultOk.final_value ?? 0)} sub={`Across ${selectedCodes.length} fund(s)`} />
                    <StatCard title="Total Invested" value={formatInr(btResultOk.total_invested ?? 0)} sub={`${btResultOk.n_contributions ?? 0} contribution(s)`} />
                    <StatCard
                      title="Absolute Gain"
                      value={formatInr(btResultOk.absolute_gain ?? 0)}
                      sub={formatSignedPct(btResultOk.absolute_return_pct ?? null)}
                      tone={toneOf(btResultOk.absolute_gain ?? null)}
                    />
                    <StatCard
                      title="XIRR (Money-Weighted)"
                      value={btResultOk.money_weighted_xirr_pct !== null && btResultOk.money_weighted_xirr_pct !== undefined ? formatSignedPct(btResultOk.money_weighted_xirr_pct) : "N/A"}
                      sub="Actual annualized return given contribution timing"
                      tone={toneOf(btResultOk.money_weighted_xirr_pct ?? null)}
                      tooltip={
                        <FormulaTooltip
                          label="XIRR"
                          formula="\sum_{i=1}^n \frac{C_i}{(1 + r)^{t_i}} = 0"
                          description="Internal Rate of Return taking into account the exact dates and amounts of every cash flow inflow/outflow."
                        />
                      }
                    />
                  </div>
                  <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-4">
                    <StatCard
                      title="Time-Weighted CAGR"
                      value={formatSignedPct(btResultOk.twr_metrics?.cagr_pct ?? null)}
                      sub="Strategy return, excludes contribution timing"
                      tone={toneOf(btResultOk.twr_metrics?.cagr_pct ?? null)}
                      tooltip={
                        <FormulaTooltip
                          label="TWR CAGR"
                          formula="\left(\prod (1 + R_t)\right)^{\frac{365.25}{\text{Days}}} - 1"
                          description="Compound Annual Growth Rate of the strategy portfolio, isolating underlying asset performance from investor deposit timing."
                        />
                      }
                    />
                    <StatCard
                      title="Sharpe Ratio (TWR)"
                      value={btResultOk.twr_metrics?.sharpe_ratio !== null && btResultOk.twr_metrics?.sharpe_ratio !== undefined ? btResultOk.twr_metrics.sharpe_ratio.toFixed(4) : "N/A"}
                      sub="Risk-adjusted return"
                      tooltip={
                        <FormulaTooltip
                          label="Sharpe Ratio"
                          formula="\frac{R_p - R_f}{\sigma_p}"
                          description="Excess annual portfolio return over risk-free rate divided by annualized portfolio volatility."
                        />
                      }
                    />
                    <StatCard
                      title="Max Drawdown (TWR)"
                      value={btResultOk.twr_metrics?.max_drawdown_pct !== null && btResultOk.twr_metrics?.max_drawdown_pct !== undefined ? `${btResultOk.twr_metrics.max_drawdown_pct.toFixed(4)}%` : "N/A"}
                      sub="Deepest peak-to-trough decline"
                      tooltip={
                        <FormulaTooltip
                          label="Max Drawdown"
                          formula="\min_t \left(\frac{V_t - \max_{\tau \le t} V_\tau}{\max_{\tau \le t} V_\tau}\right)"
                          description="Deepest historical portfolio peak-to-trough drop before establishing a new high."
                        />
                      }
                    />
                    <StatCard
                      title="Volatility (TWR, Ann.)"
                      value={btResultOk.twr_metrics?.vol_annualized_pct !== null && btResultOk.twr_metrics?.vol_annualized_pct !== undefined ? `${btResultOk.twr_metrics.vol_annualized_pct.toFixed(4)}%` : "N/A"}
                      sub="Annualized standard deviation"
                      tooltip={
                        <FormulaTooltip
                          label="Annualized Volatility"
                          formula="\sigma_{\text{daily}} \times \sqrt{252}"
                          description="Standard deviation of daily portfolio returns scaled by square root of 252 trading days."
                        />
                      }
                    />
                  </div>

                  <div className="mt-6 flex gap-1.5">
                    {(["growth", "alloc", "breakdown"] as const).map((t) => (
                      <button
                        key={t}
                        type="button"
                        onClick={() => setBacktestChartTab(t)}
                        className="rounded-full px-3 py-1.5 text-xs font-semibold"
                        style={{ background: backtestChartTab === t ? "var(--mf-accent-bg)" : "transparent", color: backtestChartTab === t ? "var(--mf-accent)" : "var(--mf-fg)" }}
                      >
                        {t === "growth" ? "Portfolio Growth" : t === "alloc" ? "Allocation Drift" : "Fund Breakdown"}
                      </button>
                    ))}
                  </div>
                  <div className="mt-3">
                    {backtestChartTab === "growth" && <PlotlyChart figure={btGrowthFigure} />}
                    {backtestChartTab === "alloc" && <PlotlyChart figure={btAllocFigure} />}
                    {backtestChartTab === "breakdown" && (
                      <DataTable
                        columns={[
                          { key: "fund", label: "Fund" },
                          { key: "target_weight_pct", label: "Target Weight %", format: "signed_pct" },
                          { key: "actual_weight_pct", label: "Actual Weight % (Today)", format: "signed_pct" },
                          { key: "current_value", label: "Current Value", format: "inr" },
                        ]}
                        rows={btBreakdownRows}
                        keyField="fund"
                      />
                    )}
                  </div>
                </>
              ) : null}
            </div>
          )}
        </>
      )}
    </AppShell>
  );
}
