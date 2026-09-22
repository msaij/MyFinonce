"use client";

import { useState, Suspense } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";

import { AppShell } from "@/components/layout/AppShell";
import { StatCard } from "@/components/shared/StatCard";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { Banner } from "@/components/shared/Banner";
import { SearchCombobox } from "@/components/shared/SearchCombobox";
import { formatSignedPct, formatInr, formatDate, toneOf } from "@/lib/format";
import { useUrlSync } from "@/lib/hooks";
import { ApiError } from "@/lib/api/client";
import { useDateRangeStore } from "@/lib/stores/dateRange";
import { useFilterStore } from "@/lib/stores/filters";
import { colorForValue, SHARPE_THRESHOLDS, SORTINO_THRESHOLDS } from "@/lib/metricThresholds";
import {
  getQuantAnalysis,
  getMonteCarlo,
  getFactorAttribution,
  getStressTest,
  getFeeDrag,
  getTailRisk,
  type BenchMode,
} from "@/lib/api/quant";
import { FactorWaterfallChart } from "@/components/quant/FactorWaterfallChart";
import { FeeDragPanel } from "@/components/quant/FeeDragPanel";
import { ScenarioStressSimulator } from "@/components/quant/ScenarioStressSimulator";

const SECTION = "quant";
const TABS = ["intel", "factors", "stress", "fees", "tail", "risk", "drawdown", "capm", "rolling", "montecarlo"] as const;
type Tab = (typeof TABS)[number];
const TAB_LABELS: Record<Tab, string> = {
  intel: "🧠 Quantitative Intelligence",
  factors: "📊 Factor Risk Attribution",
  stress: "⚡ Macro Scenario Stress Testing",
  fees: "Fee Drag",
  tail: "Tail Risk",
  risk: "Risk-Adjusted Ratios & Moments",
  drawdown: "Underwater Drawdown & Tail Risk",
  capm: "CAPM Regression & Market Capture",
  rolling: "Rolling Volatility & Sharpe",
  montecarlo: "Monte Carlo Simulation",
};

const BENCH_MODES: BenchMode[] = [
  "Category Benchmark (Synthesized Peer Average)",
  "Nifty 50 Index Fund Proxy",
  "Custom Peer Mutual Fund",
];

const QUADRANT_THEME: Record<string, { bg: string; text: string; border: string }> = {
  "Institutional Alpha Star": { bg: "var(--mf-success-bg)", text: "var(--mf-success)", border: "rgba(5, 150, 105, 0.4)" },
  "High-Beta Momentum": { bg: "var(--mf-accent-bg)", text: "var(--mf-accent)", border: "rgba(37, 99, 235, 0.4)" },
  "Defensive Anchor": { bg: "rgba(71, 85, 105, 0.12)", text: "var(--mf-fg)", border: "rgba(71, 85, 105, 0.35)" },
  "Value Trap / Laggard": { bg: "var(--mf-danger-bg)", text: "var(--mf-danger)", border: "rgba(220, 38, 38, 0.4)" },
};

function num(v: number | null | undefined, decimals = 4): string {
  return v === null || v === undefined || Number.isNaN(v) ? "N/A" : v.toFixed(decimals);
}

export default function QuantAnalysisPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm font-medium" style={{ color: "var(--mf-muted)" }}>Loading Quantitative Analysis...</div>}>
      <QuantAnalysisContent />
    </Suspense>
  );
}

function QuantAnalysisContent() {
  const searchParams = useSearchParams();
  const { start, end, planType, optionType } = useDateRangeStore();
  const getFilter = useFilterStore((s) => s.getFilter);
  const setFilter = useFilterStore((s) => s.setFilter);

  const initialCode = searchParams.get("scheme_code")
    ? Number(searchParams.get("scheme_code"))
    : getFilter(SECTION, "scheme_code", 119551);
  const initialTab = (searchParams.get("tab") as Tab) || "intel";
  const initialBench = (searchParams.get("bench_mode") as BenchMode) || BENCH_MODES[0];
  const initialPeer = searchParams.get("custom_peer_code") ? Number(searchParams.get("custom_peer_code")) : undefined;
  const initialRf = searchParams.get("rf") ? Number(searchParams.get("rf")) : 6.5;
  const initialScenario = searchParams.get("scenario") || "custom";
  const initialShockMkt = searchParams.get("shock_mkt") ? Number(searchParams.get("shock_mkt")) : 0;
  const initialShockSmb = searchParams.get("shock_smb") ? Number(searchParams.get("shock_smb")) : 0;
  const initialShockHml = searchParams.get("shock_hml") ? Number(searchParams.get("shock_hml")) : 0;
  const initialShockWml = searchParams.get("shock_wml") ? Number(searchParams.get("shock_wml")) : 0;

  const [schemeCode, setSchemeCodeState] = useState<number | null>(() => initialCode);
  const [schemeName, setSchemeName] = useState<string>(() => getFilter(SECTION, "scheme_name", ""));
  const [benchMode, setBenchModeState] = useState<BenchMode>(() =>
    BENCH_MODES.includes(initialBench) ? initialBench : BENCH_MODES[0]
  );
  const [customPeerCode, setCustomPeerCodeState] = useState<number | undefined>(() => initialPeer);
  const [customPeerName, setCustomPeerName] = useState<string>(() => getFilter(SECTION, "custom_peer_name", ""));
  const [riskFreeRatePct, setRiskFreeRatePctState] = useState<number>(() => initialRf);
  const [activeTab, setActiveTab] = useState<Tab>(() => (TABS.includes(initialTab) ? initialTab : "intel"));
  const [scenarioPreset, setScenarioPreset] = useState<string>(() => initialScenario);
  const [shockMkt, setShockMkt] = useState<number>(() => initialShockMkt);
  const [shockSmb, setShockSmb] = useState<number>(() => initialShockSmb);
  const [shockHml, setShockHml] = useState<number>(() => initialShockHml);
  const [shockWml, setShockWml] = useState<number>(() => initialShockWml);
  const [mcSeed, setMcSeed] = useState(42);

  // Synchronize state with URL query parameters for direct link sharing & bookmarking with debouncing
  useUrlSync(
    {
      scheme_code: schemeCode || undefined,
      tab: activeTab !== "intel" ? activeTab : undefined,
      bench_mode: benchMode !== BENCH_MODES[0] ? benchMode : undefined,
      custom_peer_code: customPeerCode || undefined,
      rf: riskFreeRatePct !== 6.5 ? riskFreeRatePct : undefined,
      scenario: scenarioPreset !== "custom" ? scenarioPreset : undefined,
      shock_mkt: shockMkt !== 0 ? shockMkt : undefined,
      shock_smb: shockSmb !== 0 ? shockSmb : undefined,
      shock_hml: shockHml !== 0 ? shockHml : undefined,
      shock_wml: shockWml !== 0 ? shockWml : undefined,
      start: start || undefined,
      end: end || undefined,
    },
    300
  );

  function setSchemeCode(code: number, name: string) {
    setSchemeCodeState(code);
    setSchemeName(name);
    setFilter(SECTION, "scheme_code", code);
    setFilter(SECTION, "scheme_name", name);
  }
  function setBenchMode(v: BenchMode) {
    setBenchModeState(v);
    setFilter(SECTION, "bench_mode", v);
  }
  function setCustomPeerCode(code: number, name: string) {
    setCustomPeerCodeState(code);
    setCustomPeerName(name);
    setFilter(SECTION, "custom_peer_code", code);
    setFilter(SECTION, "custom_peer_name", name);
  }
  function setRiskFreeRatePct(v: number) {
    setRiskFreeRatePctState(v);
    setFilter(SECTION, "risk_free_rate_pct", v);
  }

  const enabled = !!schemeCode && !!start && !!end && (benchMode !== "Custom Peer Mutual Fund" || !!customPeerCode);

  const {
    data: result,
    isFetching,
    isError,
    error,
  } = useQuery({
    queryKey: ["quant", schemeCode, start, end, benchMode, customPeerCode, riskFreeRatePct],
    queryFn: () =>
      getQuantAnalysis(schemeCode!, {
        startDate: start,
        endDate: end,
        benchMode,
        customPeerCode,
        riskFreeRatePct,
      }),
    enabled,
    retry: false,
    placeholderData: keepPreviousData,
  });

  const {
    data: factorsResult,
    isFetching: factorsFetching,
    isError: factorsIsError,
    error: factorsError,
  } = useQuery({
    queryKey: ["quant-factors", schemeCode, start, end, riskFreeRatePct],
    queryFn: () =>
      getFactorAttribution(schemeCode!, {
        start,
        end,
        riskFreeRatePct,
      }),
    enabled: enabled && (activeTab === "factors" || activeTab === "intel" || activeTab === "stress"),
    retry: false,
    placeholderData: keepPreviousData,
  });

  const {
    data: stressResult,
    isFetching: stressFetching,
    isError: stressIsError,
    error: stressError,
  } = useQuery({
    queryKey: ["quant-stress", schemeCode],
    queryFn: () => getStressTest(schemeCode!),
    enabled: !!schemeCode && (activeTab === "stress" || activeTab === "intel"),
    retry: false,
    placeholderData: keepPreviousData,
  });

  const {
    data: mcResult,
    isFetching: mcFetching,
    isError: mcIsError,
    error: mcError,
  } = useQuery({
    queryKey: ["quant-monte-carlo", schemeCode, start, end, mcSeed],
    queryFn: () => getMonteCarlo(schemeCode!, start, end, mcSeed),
    enabled: enabled && activeTab === "montecarlo" && (result?.coverage.n_trading_days ?? 0) >= 60,
    retry: false,
    placeholderData: keepPreviousData,
  });

  const { data: feeDrag } = useQuery({
    queryKey: ["quant-fee-drag", schemeCode],
    queryFn: () => getFeeDrag(schemeCode!),
    enabled: !!schemeCode && (activeTab === "fees" || activeTab === "intel"),
    retry: false,
  });
  const { data: tailRisk } = useQuery({
    queryKey: ["quant-tail", schemeCode, start, end],
    queryFn: () => getTailRisk(schemeCode!, start, end),
    enabled: enabled && activeTab === "tail",
    retry: false,
  });

  const metrics = result?.metrics;
  const bench = result?.benchmark;
  const figs = result?.figures;
  const intel = result?.intelligence;

  return (
    <AppShell>
      <h1 className="mf-page-title">Quantitative MF Analysis</h1>
      <p className="mf-page-caption">
        Institutional-grade quantitative intelligence, risk-adjusted scoring (Sharpe, Sortino, Calmar), tail risk (empirical VaR & CVaR), CAPM regression against benchmarks, rolling risk regimes, and forward-looking Monte Carlo paths.
      </p>
      {result && result.coverage.n_trading_days < 252 && (
        <div className="mt-3">
          <Banner level="warning">Sharpe/CAGR/factor OLS are noisy below 1 year; treat as descriptive.</Banner>
        </div>
      )}
      {benchMode.startsWith("Category") && (
        <p className="mt-2 text-xs" style={{ color: "var(--mf-muted)" }}>
          Peer average of up to 50 Direct-Growth names in this AMFI category, excluding this scheme.
        </p>
      )}

      {/* Filter toolbar */}
      <div className="filter-box mt-4 grid grid-cols-1 gap-3 md:grid-cols-6">
        <div className="flex flex-col gap-1 text-xs font-semibold md:col-span-3" style={{ color: "var(--mf-fg)" }}>
          <div className="flex items-center justify-between">
            <span>Fund</span>
            <span className="text-[0.7rem] font-bold" style={{ color: "var(--mf-accent)" }}>
              {planType} • {optionType}
            </span>
          </div>
          <SearchCombobox
            placeholder="Search fund name or AMFI code..."
            extraParams={{
              plan_type: planType !== "All Plans" ? planType : undefined,
              option_type: optionType !== "All Options" ? optionType : undefined,
            }}
            onSelect={(s) => setSchemeCode(s.scheme_code, s.scheme_name)}
          />
        </div>
        <label className="flex flex-col gap-1 text-xs font-semibold md:col-span-2" style={{ color: "var(--mf-fg)" }}>
          Benchmark
          <select
            value={benchMode}
            onChange={(e) => setBenchMode(e.target.value as BenchMode)}
            className="rounded-lg border px-2 py-1.5 text-sm font-medium"
            style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
          >
            {BENCH_MODES.map((m) => (
              <option key={m} value={m} className="bg-white text-slate-900">
                {m}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs font-semibold" style={{ color: "var(--mf-fg)" }}>
          Risk-Free Rate (%)
          <input
            type="number"
            step="0.05"
            min="0"
            max="20"
            value={riskFreeRatePct}
            onChange={(e) => setRiskFreeRatePct(Number(e.target.value))}
            className="rounded-lg border px-2 py-1.5 text-sm font-medium"
            style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
          />
        </label>
      </div>

      {benchMode === "Custom Peer Mutual Fund" && (
        <div className="filter-box mt-2">
          <div className="flex flex-col gap-1 text-xs font-semibold" style={{ color: "var(--mf-fg)" }}>
            <span>Peer Fund (Benchmark Target)</span>
            <SearchCombobox
              placeholder="Search peer mutual fund..."
              onSelect={(s) => setCustomPeerCode(s.scheme_code, s.scheme_name)}
            />
          </div>
        </div>
      )}

      {!schemeCode ? (
        <div className="mt-6">
          <Banner level="info">Search for and select a fund above to run its quantitative analysis.</Banner>
        </div>
      ) : isFetching ? (
        <p className="mt-6 text-sm font-medium" style={{ color: "var(--mf-muted)" }}>
          Computing quantitative intelligence and risk metrics...
        </p>
      ) : isError ? (
        <div className="mt-6">
          <Banner level="warning">
            {error instanceof ApiError ? error.message : "Could not analyze this fund for the selected window."}
          </Banner>
        </div>
      ) : result ? (
        <>
          {/* Fund metadata header */}
          <div className="mt-3 flex flex-wrap items-center gap-2.5 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
            <span className="font-bold text-sm" style={{ color: "var(--mf-fg)" }}>{result.profile.scheme_name}</span>
            <span>·</span>
            <span className="font-semibold" style={{ color: "var(--mf-fg)" }}>{result.profile.category ?? "Uncategorized"}</span>
            <span>·</span>
            <span className="font-semibold" style={{ color: "var(--mf-fg)" }}>{result.profile.fund_house}</span>
            <span>·</span>
            <span>
              {result.coverage.n_trading_days} trading days ({formatDate(result.coverage.actual_start)} to{" "}
              {formatDate(result.coverage.actual_end)})
            </span>
          </div>
          {result.coverage.is_partial && (
            <div className="mt-2">
              <Banner level="info">
                This fund&apos;s NAV history doesn&apos;t fully cover the selected window -- results reflect only the{" "}
                {result.coverage.n_trading_days} trading days actually available.
              </Banner>
            </div>
          )}

          {/* Executive Intelligence Brief */}
          {intel && (() => {
            const quadStyle = QUADRANT_THEME[intel.quadrant.title] ?? {
              bg: "rgba(15, 23, 42, 0.06)",
              text: "var(--mf-fg)",
              border: "var(--mf-border)",
            };
            return (
              <div
                className="mt-4 rounded-xl border p-4 shadow-sm"
                style={{
                  borderColor: "var(--mf-border)",
                  background: "linear-gradient(135deg, rgba(37, 99, 235, 0.06), rgba(5, 150, 105, 0.04))",
                }}
              >
                <div className="flex flex-wrap items-center justify-between gap-2 border-b pb-2" style={{ borderColor: "var(--mf-border)" }}>
                  <div className="flex items-center gap-2">
                    <span className="text-base font-bold" style={{ color: "var(--mf-fg)" }}>Executive Quantitative Verdict</span>
                    <span
                      className="rounded-full px-2.5 py-0.5 text-xs font-bold"
                      style={{
                        backgroundColor: quadStyle.bg,
                        color: quadStyle.text,
                        border: `1px solid ${quadStyle.border}`,
                      }}
                    >
                      {intel.quadrant.title} ({intel.quadrant.badge})
                    </span>
                  </div>
                  <span className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>
                    Automated Intelligence Model
                  </span>
                </div>
                <p className="mt-2 text-sm leading-relaxed font-medium" style={{ color: "var(--mf-fg)" }}>{intel.executive_verdict}</p>
                {(intel.quadrant.title === "Institutional Alpha Star" || intel.quadrant.title === "Value Trap / Laggard") && (
                  <p className="mt-1 text-[0.7rem]" style={{ color: "var(--mf-muted)" }}>
                    &quot;{intel.quadrant.title}&quot; is descriptive versus the category median, not a recommendation.
                  </p>
                )}
              </div>
            );
          })()}

          {/* Tab Navigation */}
          <div className="mt-6 flex flex-wrap gap-1.5 border-b pb-2" style={{ borderColor: "var(--mf-border)" }}>
            {TABS.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setActiveTab(t)}
                className="rounded-full px-3.5 py-1.5 text-xs font-bold transition-all"
                style={{
                  background: activeTab === t ? "var(--mf-accent)" : "transparent",
                  color: activeTab === t ? "#ffffff" : "var(--mf-fg)",
                  border: activeTab === t ? "1px solid var(--mf-accent)" : "1px solid var(--mf-border)",
                }}
              >
                {TAB_LABELS[t]}
              </button>
            ))}
          </div>

          {/* ================================================================= */}
          {/* TAB: QUANTITATIVE INTELLIGENCE DIAGNOSTICS                         */}
          {/* ================================================================= */}
          {activeTab === "intel" && factorsIsError && (
            <div className="mt-4">
              <Banner level="warning">
                Factor panel unavailable (FACTORS_UNAVAILABLE). Intelligence still uses windowed NAV metrics; factor loadings are omitted rather than synthesized.
              </Banner>
            </div>
          )}
          {activeTab === "intel" && intel && (
            <div className="mt-4 space-y-4">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                {/* 1. Return Drivers & Attribution */}
                <div className="rounded-xl border p-4 shadow-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <h3 className="text-sm font-bold" style={{ color: "var(--mf-fg)" }}>{intel.return_drivers.title}</h3>
                      <FormulaTooltip
                        label="Sharpe & Factor Attribution"
                        formula="\text{Sharpe} = \frac{R_p - R_f}{\sigma_p}, \quad R_p - R_f = \alpha + \beta (R_m - R_f) + \epsilon"
                        description="Decomposes total portfolio excess return into market-driven factor sensitivity (Beta) and manager stock-picking skill (Alpha)."
                      />
                    </div>
                    {(() => {
                      const sharpeTone = colorForValue(intel.return_drivers.sharpe, SHARPE_THRESHOLDS);
                      const badgeStyle =
                        sharpeTone === "pos"
                          ? { bg: "var(--mf-success-bg)", text: "#065f46", border: "rgba(5, 150, 105, 0.4)" }
                          : sharpeTone === "accent"
                          ? { bg: "var(--mf-accent-bg)", text: "#1d4ed8", border: "rgba(37, 99, 235, 0.4)" }
                          : sharpeTone === "warn"
                          ? { bg: "var(--mf-warning-bg)", text: "#92400e", border: "rgba(180, 83, 9, 0.4)" }
                          : { bg: "var(--mf-danger-bg)", text: "#b91c1c", border: "rgba(220, 38, 38, 0.4)" };
                      return (
                        <span
                          className="rounded-full px-2.5 py-0.5 text-xs font-bold"
                          style={{
                            backgroundColor: badgeStyle.bg,
                            color: badgeStyle.text,
                            border: `1px solid ${badgeStyle.border}`,
                          }}
                        >
                          Sharpe: {num(intel.return_drivers.sharpe, 2)}
                        </span>
                      );
                    })()}
                  </div>
                  <p className="mt-2 text-xs md:text-sm leading-relaxed font-normal" style={{ color: "var(--mf-fg)" }}>{intel.return_drivers.text}</p>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs">
                    <span className="rounded-md px-2.5 py-1 border font-medium" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-muted)" }}>
                      Beta: <b style={{ color: "var(--mf-fg)" }}>{intel.return_drivers.beta !== null ? num(intel.return_drivers.beta, 2) : "N/A"}</b>
                    </span>
                    <span className="rounded-md px-2.5 py-1 border font-medium" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-muted)" }}>
                      Net Alpha: <b style={{ color: toneOf(intel.return_drivers.alpha) === "pos" ? "#065f46" : toneOf(intel.return_drivers.alpha) === "neg" ? "#b91c1c" : "var(--mf-fg)" }}>{intel.return_drivers.alpha !== null ? formatSignedPct(intel.return_drivers.alpha, 2) : "N/A"}</b>
                    </span>
                    <span className="rounded-md px-2.5 py-1 border font-medium" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-muted)" }}>
                      Sortino: <b style={{ color: "var(--mf-fg)" }}>{num(intel.return_drivers.sortino, 2)}</b>
                    </span>
                  </div>
                </div>

                {/* 2. Style Drift & Benchmark Fit */}
                <div className="rounded-xl border p-4 shadow-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <h3 className="text-sm font-bold" style={{ color: "var(--mf-fg)" }}>{intel.style_drift.title}</h3>
                      <FormulaTooltip
                        label="Tracking Error & R-Squared"
                        formula="\text{TE} = \sigma(R_p - R_b) \times \sqrt{252}, \quad R^2 = \text{Corr}(R_p, R_b)^2"
                        description="Measures annualized active deviation against benchmark (Tracking Error) and variance explained by benchmark moves (R-squared)."
                      />
                    </div>
                    <span className="rounded-full px-2.5 py-0.5 text-xs font-bold" style={{ backgroundColor: "var(--mf-accent-bg)", color: "#1d4ed8", border: "1px solid rgba(37, 99, 235, 0.4)" }}>
                      {intel.style_drift.assessment}
                    </span>
                  </div>
                  <p className="mt-2 text-xs md:text-sm leading-relaxed font-normal" style={{ color: "var(--mf-fg)" }}>{intel.style_drift.text}</p>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs">
                    <span className="rounded-md px-2.5 py-1 border font-medium" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-muted)" }}>
                      R²: <b style={{ color: "var(--mf-fg)" }}>{intel.style_drift.r_squared !== null ? num(intel.style_drift.r_squared, 2) : "N/A"}</b>
                    </span>
                    <span className="rounded-md px-2.5 py-1 border font-medium" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-muted)" }}>
                      Tracking Error: <b style={{ color: "var(--mf-fg)" }}>{intel.style_drift.tracking_error_pct !== null ? `${num(intel.style_drift.tracking_error_pct, 2)}%` : "N/A"}</b>
                    </span>
                    <span className="rounded-md px-2.5 py-1 border font-medium" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-muted)" }}>
                      Information Ratio: <b style={{ color: "var(--mf-fg)" }}>{intel.style_drift.information_ratio !== null ? num(intel.style_drift.information_ratio, 2) : "N/A"}</b>
                    </span>
                  </div>
                </div>

                {/* 3. Volatility Regime & Tail Risk */}
                <div className="rounded-xl border p-4 shadow-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <h3 className="text-sm font-bold" style={{ color: "var(--mf-fg)" }}>{intel.tail_risk.title}</h3>
                      <FormulaTooltip
                        label="Tail Risk: VaR & CVaR"
                        formula="\text{VaR}_{95} = -Q_{0.05}(R), \quad \text{CVaR}_{95} = -\mathbb{E}[R \mid R \le -\text{VaR}_{95}]"
                        description="Empirical 95% 1-day Value at Risk (threshold loss) and Conditional VaR / Expected Shortfall (average loss beyond threshold)."
                      />
                    </div>
                    <span className="rounded-full px-2.5 py-0.5 text-xs font-bold" style={{ backgroundColor: "var(--mf-danger-bg)", color: "#b91c1c", border: "1px solid rgba(220, 38, 38, 0.4)" }}>
                      Max DD: -{num(intel.tail_risk.max_drawdown_pct, 2)}%
                    </span>
                  </div>
                  <p className="mt-2 text-xs md:text-sm leading-relaxed font-normal" style={{ color: "var(--mf-fg)" }}>{intel.tail_risk.text}</p>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs">
                    <span className="rounded-md px-2.5 py-1 border font-medium" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-muted)" }}>
                      Skewness: <b style={{ color: "var(--mf-fg)" }}>{num(intel.tail_risk.skewness, 2)}</b>
                    </span>
                    <span className="rounded-md px-2.5 py-1 border font-medium" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-muted)" }}>
                      Excess Kurtosis: <b style={{ color: "var(--mf-fg)" }}>{num(intel.tail_risk.kurtosis, 2)}</b>
                    </span>
                    <span className="rounded-md px-2.5 py-1 border font-medium" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-muted)" }}>
                      1D VaR 95%: <b style={{ color: "var(--mf-fg)" }}>{num(intel.tail_risk.var_95_daily_pct, 2)}%</b>
                    </span>
                    <span className="rounded-md px-2.5 py-1 border font-medium" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-muted)" }}>
                      Ann. CVaR 95%: <b style={{ color: "var(--mf-fg)" }}>{num(intel.tail_risk.cvar_95_ann_pct, 2)}%</b>
                    </span>
                  </div>
                </div>

                {/* 4. Fee Drag & Compounding Impact */}
                <div className="rounded-xl border p-4 shadow-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <h3 className="text-sm font-bold" style={{ color: "var(--mf-fg)" }}>{intel.fee_drag.title}</h3>
                      <FormulaTooltip
                        label="Compounded Fee Drag Formula"
                        formula="V_{\text{drag}} = V_0 \cdot \left((1 + r)^T - (1 + r - \text{TER})^T\right)"
                        description="Estimates wealth lost to ongoing expense ratios and commissions compounded over multi-year holding periods."
                      />
                    </div>
                    <span
                      className="rounded-full px-2.5 py-0.5 text-xs font-bold"
                      style={{
                        backgroundColor: intel.fee_drag.is_direct ? "var(--mf-success-bg)" : "var(--mf-warning-bg)",
                        color: intel.fee_drag.is_direct ? "#065f46" : "#92400e",
                        border: `1px solid ${intel.fee_drag.is_direct ? "rgba(5, 150, 105, 0.4)" : "rgba(217, 119, 6, 0.4)"}`,
                      }}
                    >
                      {intel.fee_drag.is_direct ? "Direct Plan (Zero Commission)" : "Regular Plan (Commission Drag)"}
                    </span>
                  </div>
                  <p className="mt-2 text-xs md:text-sm leading-relaxed font-normal" style={{ color: "var(--mf-fg)" }}>{intel.fee_drag.text}</p>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs">
                    <span className="rounded-md px-2.5 py-1 border font-medium" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-muted)" }}>
                      Expense Ratio: <b style={{ color: "var(--mf-fg)" }}>{intel.fee_drag.ter_pct !== null ? `${intel.fee_drag.ter_pct.toFixed(2)}%` : "N/A"}</b>
                    </span>
                    {intel.fee_drag.gross_alpha_pct !== null && (
                      <span className="rounded-md px-2.5 py-1 border font-medium" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-muted)" }}>
                        Pre-TER Gross Alpha: <b style={{ color: toneOf(intel.fee_drag.gross_alpha_pct) === "pos" ? "var(--mf-success)" : "var(--mf-danger)" }}>{formatSignedPct(intel.fee_drag.gross_alpha_pct, 2)}</b>
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {/* 5. Market Capture Asymmetry banner */}
              <div className="rounded-xl border p-4 shadow-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <h3 className="text-sm font-bold" style={{ color: "var(--mf-fg)" }}>{intel.market_capture.title}</h3>
                    <FormulaTooltip
                      label="Up / Down Market Capture"
                      formula="\text{UpCap} = \frac{R_{p,\text{up}}}{R_{m,\text{up}}}, \quad \text{DownCap} = \frac{R_{p,\text{down}}}{R_{m,\text{down}}}"
                      description="Quantifies percentage of benchmark gains captured during up periods vs losses absorbed during down periods."
                    />
                  </div>
                  {(() => {
                    const capRatio = intel.market_capture.capture_ratio;
                    const isGood = capRatio !== null && capRatio >= 1.0;
                    const isBad = capRatio !== null && capRatio < 1.0;
                    const capBg = isGood ? "var(--mf-success-bg)" : isBad ? "var(--mf-warning-bg)" : "rgba(71, 85, 105, 0.12)";
                    const capText = isGood ? "var(--mf-success)" : isBad ? "var(--mf-warning)" : "var(--mf-fg)";
                    const capBorder = isGood ? "rgba(5, 150, 105, 0.4)" : isBad ? "rgba(217, 119, 6, 0.4)" : "rgba(71, 85, 105, 0.35)";
                    return (
                      <span className="rounded-full px-2.5 py-0.5 text-xs font-bold" style={{ backgroundColor: capBg, color: capText, border: `1px solid ${capBorder}` }}>
                        {intel.market_capture.verdict}
                      </span>
                    );
                  })()}
                </div>
                <p className="mt-2 text-xs md:text-sm leading-relaxed font-normal" style={{ color: "var(--mf-fg)" }}>{intel.market_capture.text}</p>
                {intel.market_capture.up_market_capture_pct !== null && intel.market_capture.down_market_capture_pct !== null && (
                  <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
                    <div className="rounded-lg p-2.5 border" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)" }}>
                      <div className="text-[0.72rem] font-bold uppercase tracking-wider" style={{ color: "var(--mf-muted)" }}>Up-Market Capture</div>
                      <div className="text-base font-extrabold mt-0.5" style={{ color: "var(--mf-success)" }}>{num(intel.market_capture.up_market_capture_pct, 1)}%</div>
                    </div>
                    <div className="rounded-lg p-2.5 border" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)" }}>
                      <div className="text-[0.72rem] font-bold uppercase tracking-wider" style={{ color: "var(--mf-muted)" }}>Down-Market Capture</div>
                      <div className="text-base font-extrabold mt-0.5" style={{ color: "var(--mf-danger)" }}>{num(intel.market_capture.down_market_capture_pct, 1)}%</div>
                    </div>
                    <div className="rounded-lg p-2.5 border" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)" }}>
                      <div className="text-[0.72rem] font-bold uppercase tracking-wider" style={{ color: "var(--mf-muted)" }}>Capture Ratio</div>
                      <div className="text-base font-extrabold mt-0.5" style={{ color: "var(--mf-accent)" }}>{num(intel.market_capture.capture_ratio, 2)}x</div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ================================================================= */}
          {/* TAB: FACTOR RISK ATTRIBUTION (M4)                                 */}
          {/* ================================================================= */}
          {activeTab === "factors" && (
            <div className="mt-4 space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h3 className="text-base font-bold" style={{ color: "var(--mf-fg)" }}>
                    Multi-Factor Risk Attribution (Fama-French-Carhart 4-Factor)
                  </h3>
                  <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
                    OLS decomposition across Market (Nifty 50), Size (MidSmall spread), Value, and Momentum (omitted when the momentum proxy is missing). Minimum 60 overlapping days. SMB/HML include this scheme in the category average.
                  </p>
                </div>
                <FormulaTooltip
                  label="Multi-Factor Return Decomposition"
                  formula="R_i - R_f = \alpha + \beta_{\text{mkt}} MKT + \beta_{\text{smb}} SMB + \beta_{\text{hml}} HML + \beta_{\text{wml}} WML + \epsilon"
                  description="Separates fund returns into systematic factor risk premia and true manager skill (Alpha), controlling for size, value, and momentum style exposures."
                />
              </div>

              {factorsFetching && !factorsResult && (
                <div className="p-8 text-center text-xs" style={{ color: "var(--mf-muted)" }}>
                  Computing multivariate factor attribution...
                </div>
              )}

              {factorsIsError && (
                <Banner level="warning">
                  {(factorsError as any)?.message ?? "Factor attribution could not be calculated (minimum 60 overlapping trading days; no synthetic factors)."}
                </Banner>
              )}

              {factorsResult?.source?.disclosure && (
                <Banner level="info">{factorsResult.source.disclosure} WML method: {factorsResult.source.wml_method}.</Banner>
              )}
              {factorsResult && (
                <>
                  {/* Factor Waterfall Chart */}
                  <div className="rounded-xl border p-4 shadow-sm" style={{ background: "var(--mf-card-bg)", borderColor: "var(--mf-border)" }}>
                    <FactorWaterfallChart
                      alphaAnnPct={factorsResult.regression.alpha_annualized_pct}
                      waterfallData={factorsResult.regression.waterfall_data}
                      schemeName={result?.profile.scheme_name}
                      figure={factorsResult.figures.factor_waterfall}
                    />
                  </div>

                  {/* Factor Regression Statistics & Betas */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <StatCard
                      title="Manager Alpha (Ann.)"
                      value={formatSignedPct(factorsResult.regression.alpha_annualized_pct)}
                      sub={`t-stat: ${num(factorsResult.regression.t_stats?.alpha, 2)} (p=${num(factorsResult.regression.p_values?.alpha, 3)})`}
                      tone={toneOf(factorsResult.regression.alpha_annualized_pct)}
                    />
                    <StatCard
                      title="R-Squared (R²)"
                      value={`${(factorsResult.regression.r_squared * 100).toFixed(2)}%`}
                      sub={`Adj. R²: ${(factorsResult.regression.adj_r_squared * 100).toFixed(2)}%`}
                    />
                    <StatCard
                      title="Systematic Risk"
                      value={`${(factorsResult.regression.systematic_risk_pct).toFixed(1)}%`}
                      sub="Variance from factors"
                    />
                    <StatCard
                      title="Idiosyncratic Risk"
                      value={`${(factorsResult.regression.idiosyncratic_risk_pct).toFixed(1)}%`}
                      sub="Specific fund risk"
                    />
                  </div>

                  {/* Factor Betas & Variance Decomposition Table */}
                  <div className="rounded-xl border p-4 shadow-sm" style={{ background: "var(--mf-card-bg)", borderColor: "var(--mf-border)" }}>
                    <h4 className="text-sm font-bold mb-3" style={{ color: "var(--mf-fg)" }}>
                      Factor Sensitivities (Betas) & Variance Contribution
                    </h4>
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs text-left border-collapse">
                        <thead>
                          <tr className="border-b" style={{ borderColor: "var(--mf-border)", color: "var(--mf-muted)" }}>
                            <th className="py-2 px-3 font-semibold">Factor</th>
                            <th className="py-2 px-3 font-semibold text-right">Factor Beta (β)</th>
                            <th className="py-2 px-3 font-semibold text-right">t-statistic</th>
                            <th className="py-2 px-3 font-semibold text-right">p-value</th>
                            <th className="py-2 px-3 font-semibold text-right">% Variance Explained</th>
                          </tr>
                        </thead>
                        <tbody>
                          {[
                            { name: "Market (Nifty 50 Excess)", key: "market", k2: "mkt_excess" },
                            { name: "Size (SMB - MidSmall Spread)", key: "size", k2: "smb" },
                            { name: "Value (HML - Contra Spread)", key: "value", k2: "hml" },
                            { name: "Momentum (WML Spread)", key: "momentum", k2: "wml" },
                          ].map((f) => {
                            const beta = factorsResult.regression.factor_betas[f.key] ?? factorsResult.regression.factor_betas[f.k2] ?? 0;
                            const tstat = factorsResult.regression.t_stats[f.key] ?? factorsResult.regression.t_stats[f.k2] ?? 0;
                            const pval = factorsResult.regression.p_values[f.key] ?? factorsResult.regression.p_values[f.k2] ?? 0;
                            const vdecomp = factorsResult.regression.variance_decomposition[f.key] ?? factorsResult.regression.variance_decomposition[f.k2] ?? 0;
                            return (
                              <tr key={f.key} className="border-b last:border-b-0 hover:bg-slate-500/5 transition-colors" style={{ borderColor: "var(--mf-border)" }}>
                                <td className="py-2.5 px-3 font-semibold" style={{ color: "var(--mf-fg)" }}>{f.name}</td>
                                <td className="py-2.5 px-3 text-right font-mono font-bold" style={{ color: beta >= 0 ? "var(--mf-accent)" : "var(--mf-danger)" }}>
                                  {beta.toFixed(4)}
                                </td>
                                <td className="py-2.5 px-3 text-right font-mono" style={{ color: "var(--mf-muted)" }}>{tstat.toFixed(2)}</td>
                                <td className="py-2.5 px-3 text-right font-mono" style={{ color: pval < 0.05 ? "var(--mf-success)" : "var(--mf-muted)" }}>
                                  {pval.toFixed(4)} {pval < 0.05 ? "★" : ""}
                                </td>
                                <td className="py-2.5 px-3 text-right font-mono font-semibold" style={{ color: "var(--mf-fg)" }}>
                                  {vdecomp.toFixed(2)}%
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  {factorsResult.figures.factor_decomposition && (
                    <div className="rounded-xl border p-4 shadow-sm" style={{ background: "var(--mf-card-bg)", borderColor: "var(--mf-border)" }}>
                      <PlotlyChart figure={factorsResult.figures.factor_decomposition} />
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* ================================================================= */}
          {/* TAB: MACRO SCENARIO STRESS TESTING (M4)                           */}
          {/* ================================================================= */}
          {activeTab === "stress" && (factorsIsError || stressIsError) && (
            <div className="mt-4">
              {factorsIsError && (
                <Banner level="warning">Factor panel unavailable. Historical crisis replay is shown; parametric shocks are disabled until factor OLS succeeds (60 overlapping days).</Banner>
              )}
              {stressIsError && (
                <Banner level="warning">{(stressError as { message?: string })?.message ?? "Historical stress replay could not be loaded."}</Banner>
              )}
            </div>
          )}
          {activeTab === "stress" && (() => {
            return (
              <div className="mt-4 space-y-4">
                {factorsResult?.regression.factor_betas ? (
                <ScenarioStressSimulator
                  schemeCode={schemeCode ?? undefined}
                  schemeName={result?.profile.scheme_name ?? `Scheme ${schemeCode}`}
                  category={result?.profile.category ?? undefined}
                  factorBetas={factorsResult.regression.factor_betas}
                  baselineMaxDrawdown={result?.metrics.max_drawdown_pct}
                  historicalScenarios={stressResult?.scenarios}
                  initialPreset={scenarioPreset}
                  initialShocks={{
                    market: shockMkt,
                    size: shockSmb,
                    value: shockHml,
                    momentum: shockWml,
                  }}
                  onShocksChange={(nextShocks, nextPreset) => {
                    setShockMkt(nextShocks.market);
                    setShockSmb(nextShocks.size);
                    setShockHml(nextShocks.value);
                    setShockWml(nextShocks.momentum);
                    setScenarioPreset(nextPreset);
                  }}
                />
                ) : (
                  <Banner level="info">Parametric factor-shock simulator is disabled until factor OLS succeeds (minimum 60 overlapping days). Historical crisis replay below is independent of that panel.</Banner>
                )}

              {stressResult?.figure && (
                <div className="rounded-xl border p-5 shadow-sm" style={{ background: "var(--mf-card-bg)", borderColor: "var(--mf-border)" }}>
                  <h4 className="text-sm font-bold mb-2" style={{ color: "var(--mf-fg)" }}>
                    Historical Crisis Peak-to-Trough Drawdown vs Benchmark
                  </h4>
                  <PlotlyChart figure={stressResult.figure} />
                </div>
              )}
            </div>
          );
        })()}

          {/* ================================================================= */}
          {/* TAB: RISK-ADJUSTED METRICS                                         */}
          {/* ================================================================= */}
          {activeTab === "risk" && metrics && (
            <div className="mt-4">
              <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                <StatCard
                  title="Sharpe Ratio"
                  value={num(metrics.sharpe_ratio)}
                  sub="Return per unit of total risk"
                  tone={colorForValue(metrics.sharpe_ratio, SHARPE_THRESHOLDS)}
                  tooltip={
                    <FormulaTooltip
                      label="Sharpe Ratio"
                      formula="\text{Sharpe} = \frac{R_p - R_f}{\sigma_p}"
                      description="(Annualized Return - Risk-Free Rate) / Annualized Volatility. >1.0 excellent, >0.5 good, >=0 fair, <0 poor."
                    />
                  }
                />
                <StatCard
                  title="Sortino Ratio"
                  value={num(metrics.sortino_ratio)}
                  sub="Return per unit of downside risk"
                  tone={colorForValue(metrics.sortino_ratio, SORTINO_THRESHOLDS)}
                  tooltip={
                    <FormulaTooltip
                      label="Sortino Ratio"
                      formula="\text{Sortino} = \frac{R_p - R_f}{\sigma_d}"
                      description="Like Sharpe, but only penalizes downside volatility relative to risk-free rate. >1.5 excellent, >0.8 good."
                    />
                  }
                />
                <StatCard
                  title="Calmar Ratio"
                  value={metrics.calmar_ratio !== null ? num(metrics.calmar_ratio) : "N/A"}
                  sub="CAGR / |Max Drawdown|"
                  tooltip={
                    <FormulaTooltip
                      label="Calmar Ratio"
                      formula="\text{Calmar} = \frac{\text{CAGR}}{|\text{Max Drawdown}|}"
                      description="Annualized return relative to the worst peak-to-trough loss over the window."
                    />
                  }
                />
                <StatCard title="CAGR" value={formatSignedPct(metrics.cagr_pct, 2)} sub="Annualized return" tone={toneOf(metrics.cagr_pct)} />
                <StatCard title="Total Return" value={formatSignedPct(metrics.total_return_pct, 2)} sub={`Over ${metrics.total_days} calendar days`} tone={toneOf(metrics.total_return_pct)} />
                <StatCard title="Volatility (Ann.)" value={`${num(metrics.vol_annualized_pct, 2)}%`} sub="Annualized std. deviation (σ × √252)" />
                <StatCard title="Win Rate" value={`${num(metrics.win_rate_pct, 2)}%`} sub="Share of positive trading days" />
                <StatCard title="Profit Factor" value={metrics.profit_factor !== null ? num(metrics.profit_factor, 2) : "N/A"} sub="Gross gains / gross losses" />
                <StatCard title="Gain-to-Pain Ratio" value={metrics.gain_to_pain_ratio !== null ? num(metrics.gain_to_pain_ratio, 2) : "N/A"} sub="(Gains - Losses) / Losses" />
                <StatCard title="Best Day" value={formatSignedPct(metrics.best_day_pct, 2)} tone="pos" />
                <StatCard title="Worst Day" value={formatSignedPct(metrics.worst_day_pct, 2)} tone="neg" />
                <StatCard title="Skewness" value={num(metrics.skewness, 3)} sub="Return distribution asymmetry" />
                <StatCard title="Kurtosis" value={num(metrics.kurtosis, 3)} sub="Excess kurtosis (Fisher, Normal=0)" />
              </div>
              {figs?.cumulative_return && (
                <div className="mt-6 rounded-xl border p-5 shadow-sm" style={{ background: "var(--mf-card-bg)", borderColor: "var(--mf-border)" }}>
                  <h3 className="text-base font-bold mb-3" style={{ color: "var(--mf-fg)" }}>Cumulative Return Trajectory</h3>
                  <PlotlyChart figure={figs.cumulative_return} />
                </div>
              )}
              {figs?.distribution && (
                <div className="mt-6 rounded-xl border p-5 shadow-sm" style={{ background: "var(--mf-card-bg)", borderColor: "var(--mf-border)" }}>
                  <h3 className="text-base font-bold mb-3" style={{ color: "var(--mf-fg)" }}>Daily Return Distribution & Gaussian Fit</h3>
                  <PlotlyChart figure={figs.distribution} />
                </div>
              )}
            </div>
          )}

          {/* ================================================================= */}
          {/* TAB: UNDERWATER DRAWDOWN & TAIL RISK                              */}
          {/* ================================================================= */}
          {activeTab === "drawdown" && metrics && (
            <div className="mt-4">
              <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                <StatCard title="Max Drawdown" value={`${num(metrics.max_drawdown_pct, 2)}%`} sub="Deepest peak-to-trough decline" tone="neg" />
                <StatCard
                  title="Current Drawdown"
                  value={`${num(metrics.current_drawdown_pct, 2)}%`}
                  sub="Below window peak, right now"
                  tone={metrics.current_drawdown_pct < -0.01 ? "warn" : "pos"}
                />
                <StatCard
                  title="VaR 95% (Daily)"
                  value={`${num(metrics.var_95_daily_pct, 2)}%`}
                  sub="Worst expected daily loss, 95% confidence"
                  tooltip={
                    <FormulaTooltip
                      label="Value at Risk (95% Daily)"
                      formula="\text{VaR}_{95} = -Q_{0.05}(R_{\text{daily}})"
                      description="5th percentile of daily return distribution -- on 95% of days, loss does not exceed this."
                    />
                  }
                />
                <StatCard title="VaR 99% (Daily)" value={`${num(metrics.var_99_daily_pct, 2)}%`} sub="Worst expected daily loss, 99% confidence" />
                <StatCard
                  title="CVaR 95% (Daily)"
                  value={`${num(metrics.cvar_95_daily_pct, 2)}%`}
                  sub="Average loss on worst 5% of days"
                  tooltip={
                    <FormulaTooltip
                      label="Conditional VaR (Expected Shortfall)"
                      formula="\text{CVaR}_{95} = -\mathbb{E}[R \mid R \le -\text{VaR}_{95}]"
                      description="Expected Shortfall -- the average of all losses beyond the 95% VaR threshold."
                    />
                  }
                />
                <StatCard title="VaR 95% (Annualized)" value={`${num(metrics.var_95_ann_pct, 2)}%`} />
                <StatCard title="CVaR 95% (Annualized)" value={`${num(metrics.cvar_95_ann_pct, 2)}%`} />
              </div>
              {figs?.drawdown && (
                <div className="mt-6 rounded-xl border p-5 shadow-sm" style={{ background: "var(--mf-card-bg)", borderColor: "var(--mf-border)" }}>
                  <h3 className="text-base font-bold mb-3" style={{ color: "var(--mf-fg)" }}>Underwater Drawdown Profile</h3>
                  <PlotlyChart figure={figs.drawdown} />
                </div>
              )}
            </div>
          )}

          {/* ================================================================= */}
          {/* TAB: CAPM REGRESSION & MARKET CAPTURE                            */}
          {/* ================================================================= */}
          {activeTab === "capm" && (
            <div className="mt-4">
              {!bench?.available ? (
                <Banner level="warning">{bench?.unavailable_reason ?? "Benchmark comparison is not available for this selection."}</Banner>
              ) : (
                <>
                  <p className="text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
                    Benchmarked against: <b style={{ color: "var(--mf-fg)" }}>{bench.label}</b>
                  </p>
                  <div className="mt-3 grid grid-cols-2 gap-4 md:grid-cols-4">
                    <StatCard
                      title="Beta"
                      value={num(bench.metrics!.beta, 2)}
                      sub="Sensitivity to benchmark moves"
                      tooltip={
                        <FormulaTooltip
                          label="Beta (CAPM)"
                          formula="\beta = \frac{\text{Cov}(R_p, R_m)}{\text{Var}(R_m)}"
                          description="1.0 = moves in tandem with benchmark; >1.0 amplifies swings; <1.0 dampens swings."
                        />
                      }
                    />
                    <StatCard
                      title="Alpha (Annualized)"
                      value={formatSignedPct(bench.metrics!.alpha_annualized_pct, 2)}
                      sub="Excess return vs CAPM expectation"
                      tone={toneOf(bench.metrics!.alpha_annualized_pct)}
                    />
                    {result.gross_alpha_pct !== null && (
                      <StatCard
                        title="Gross Alpha (Pre-TER)"
                        value={formatSignedPct(result.gross_alpha_pct, 2)}
                        sub="Net Alpha + official TER"
                        tone={toneOf(result.gross_alpha_pct)}
                      />
                    )}
                    <StatCard title="R-Squared" value={num(bench.metrics!.r_squared, 2)} sub="Variance explained by benchmark" />
                    <StatCard title="Tracking Error" value={`${num(bench.metrics!.tracking_error_pct, 2)}%`} sub="Annualized active deviation" />
                    <StatCard title="Information Ratio" value={num(bench.metrics!.information_ratio, 2)} sub="Excess return per active risk" />
                    <StatCard title="Treynor Ratio" value={num(bench.metrics!.treynor_ratio, 2)} sub="Excess return per unit of Beta" />
                    <StatCard title="Capture Ratio" value={bench.metrics!.capture_ratio !== null ? num(bench.metrics!.capture_ratio, 2) : "N/A"} sub="Up-capture / Down-capture" />
                  </div>
                  {figs?.capm_regression && (
                    <div className="mt-6 rounded-xl border p-5 shadow-sm" style={{ background: "var(--mf-card-bg)", borderColor: "var(--mf-border)" }}>
                      <h3 className="text-base font-bold mb-3" style={{ color: "var(--mf-fg)" }}>CAPM Regression Scatter & Best-Fit Line</h3>
                      <PlotlyChart figure={figs.capm_regression} />
                    </div>
                  )}
                  {figs?.capture && (
                    <div className="mt-6 rounded-xl border p-5 shadow-sm" style={{ background: "var(--mf-card-bg)", borderColor: "var(--mf-border)" }}>
                      <h3 className="text-base font-bold mb-3" style={{ color: "var(--mf-fg)" }}>Up / Down Market Capture Breakdown</h3>
                      <PlotlyChart figure={figs.capture} />
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* ================================================================= */}
          {/* TAB: ROLLING VOLATILITY & SHARPE                                 */}
          {/* ================================================================= */}
          {activeTab === "rolling" && (
            <div className="mt-4">
              <p className="text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
                {result.rolling_window}-day rolling window{result.rolling_window === 10 ? " (reduced from 30 due to short history)" : ""}.
              </p>
              {!figs?.rolling_volatility && !figs?.rolling_sharpe ? (
                <div className="mt-3">
                  <Banner level="info">Not enough trading days in this window to compute rolling metrics.</Banner>
                </div>
              ) : (
                <>
                  {figs?.rolling_volatility && (
                    <div className="mt-6 rounded-xl border p-5 shadow-sm" style={{ background: "var(--mf-card-bg)", borderColor: "var(--mf-border)" }}>
                      <h3 className="text-base font-bold mb-3" style={{ color: "var(--mf-fg)" }}>Rolling Annualized Volatility Regime</h3>
                      <PlotlyChart figure={figs.rolling_volatility} />
                    </div>
                  )}
                  {figs?.rolling_sharpe && (
                    <div className="mt-6 rounded-xl border p-5 shadow-sm" style={{ background: "var(--mf-card-bg)", borderColor: "var(--mf-border)" }}>
                      <h3 className="text-base font-bold mb-3" style={{ color: "var(--mf-fg)" }}>Rolling Sharpe Ratio Trend</h3>
                      <PlotlyChart figure={figs.rolling_sharpe} />
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* ================================================================= */}
          {/* TAB: MONTE CARLO SIMULATION                                       */}
          {/* ================================================================= */}
          {activeTab === "fees" && (
            <div className="mt-4 space-y-3">
              <h3 className="text-base font-bold">Fee-drag attribution</h3>
              <FeeDragPanel data={feeDrag as Record<string, unknown> | undefined} />
            </div>
          )}

          {activeTab === "tail" && (
            <div className="mt-4 space-y-3">
              <h3 className="text-base font-bold">Tail risk</h3>
              {(() => {
                const tr = (tailRisk as { tail_risk?: Record<string, number> } | undefined)?.tail_risk;
                if (!tr) return <Banner level="info">Select a window of at least a few dozen sessions.</Banner>;
                return (
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                    <StatCard title="VaR 95% (daily)" value={tr.var_95_daily_pct != null ? `${tr.var_95_daily_pct.toFixed(3)}%` : "—"} />
                    <StatCard title="CVaR 95% (ann.)" value={tr.cvar_95_ann_pct != null ? `${tr.cvar_95_ann_pct.toFixed(3)}%` : "—"} />
                    <StatCard title="VaR 99% (daily)" value={tr.var_99_daily_pct != null ? `${tr.var_99_daily_pct.toFixed(3)}%` : "—"} />
                    <StatCard title="Max drawdown" value={tr.max_drawdown_pct != null ? `${tr.max_drawdown_pct.toFixed(3)}%` : "—"} />
                  </div>
                );
              })()}
            </div>
          )}

          {activeTab === "montecarlo" && (
            <div className="mt-4">
              {(result?.coverage.n_trading_days ?? 0) < 60 && (
                <Banner level="warning">Monte Carlo is disabled below 60 sessions.</Banner>
              )}
              <Banner level="info">
                i.i.d. GBM calibrated on this window — ignores fat tails, serial correlation, and regime. Inflation hurdle is a hardcoded 6% (`initial_capital * 1.06`), not a live CPI series.
              </Banner>
              <div className="mt-3 flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => setMcSeed((s) => s + 1)}
                  className="rounded-lg px-4 py-2 text-sm font-bold shadow-sm transition-all"
                  style={{ background: "var(--mf-accent)", color: "#ffffff" }}
                >
                  Re-run Simulation (New Stochastic Seed)
                </button>
                <span className="text-xs font-medium" style={{ color: "var(--mf-muted)" }}>Seed: <b style={{ color: "var(--mf-fg)" }}>{mcSeed}</b></span>
              </div>
              {mcFetching ? (
                <p className="mt-4 text-sm font-medium" style={{ color: "var(--mf-muted)" }}>
                  Simulating 500 stochastic trajectories...
                </p>
              ) : mcIsError ? (
                <div className="mt-4">
                  <Banner level="warning">
                    {mcError instanceof ApiError ? mcError.message : "Could not run Monte Carlo simulation for this fund."}
                  </Banner>
                </div>
              ) : mcResult ? (
                <>
                  <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-5">
                    <StatCard
                      title="P(Profit)"
                      value={`${num(mcResult.prob_profit_pct, 1)}%`}
                      sub="Ends above ₹1,00,000"
                      tone={mcResult.prob_profit_pct >= 50 ? "pos" : "warn"}
                    />
                    <StatCard title="P(Beat Inflation)" value={`${num(mcResult.prob_beat_inflation_pct, 1)}%`} sub="Ends above +6%" />
                    <StatCard title="P(Beat 12%)" value={`${num(mcResult.prob_beat_12pct, 1)}%`} sub="Ends above +12%" />
                    <StatCard title="Median Terminal" value={formatInr(mcResult.median_terminal)} sub="50th percentile outcome" />
                    <StatCard title="VaR 95% (Capital)" value={formatInr(mcResult.var_95_capital)} sub="Capital at risk, 95% confidence" tone="neg" />
                  </div>

                  {/* Confidence Intervals */}
                  {mcResult.ci_90 && (
                    <div className="mt-3 rounded-xl border p-4 text-xs shadow-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
                      <div className="font-bold text-sm" style={{ color: "var(--mf-fg)" }}>Monte Carlo Confidence Intervals (1-Year Horizon)</div>
                      <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-3">
                        <div className="rounded-lg p-2.5 border" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)" }}>
                          <span className="font-bold text-xs" style={{ color: "var(--mf-muted)" }}>90% Confidence Interval (P5 to P95):</span>
                          <div className="font-extrabold text-sm mt-0.5" style={{ color: "#065f46" }}>{formatInr(mcResult.ci_90[0])} — {formatInr(mcResult.ci_90[1])}</div>
                        </div>
                        {mcResult.ci_50 && (
                          <div className="rounded-lg p-2.5 border" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)" }}>
                            <span className="font-bold text-xs" style={{ color: "var(--mf-muted)" }}>50% Interquartile Range (P25 to P75):</span>
                            <div className="font-extrabold text-sm mt-0.5" style={{ color: "#1d4ed8" }}>{formatInr(mcResult.ci_50[0])} — {formatInr(mcResult.ci_50[1])}</div>
                          </div>
                        )}
                        {mcResult.expected_terminal && (
                          <div className="rounded-lg p-2.5 border" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)" }}>
                            <span className="font-bold text-xs" style={{ color: "var(--mf-muted)" }}>Expected Terminal Value:</span>
                            <div className="font-extrabold text-sm mt-0.5" style={{ color: "var(--mf-fg)" }}>{formatInr(mcResult.expected_terminal)}</div>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  <div className="mt-4">
                    <PlotlyChart figure={mcResult.figure} />
                  </div>
                </>
              ) : null}
            </div>
          )}
        </>
      ) : null}
    </AppShell>
  );
}
