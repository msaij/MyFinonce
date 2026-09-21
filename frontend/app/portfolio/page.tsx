"use client";

import { useEffect, useMemo, useState, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";

import { AppShell } from "@/components/layout/AppShell";
import { StatCard } from "@/components/shared/StatCard";
import { DataTable, ColumnConfig } from "@/components/shared/DataTable";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { Banner } from "@/components/shared/Banner";
import { NonAdviceDisclaimer } from "@/components/shared/Disclaimer";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { StatusPill } from "@/components/layout/StatusPill";
import { formatSignedPct, formatInr, toneOf, CHART_MUTED_COLOR } from "@/lib/format";
import { useUrlSync } from "@/lib/hooks";
import { useFilterStore } from "@/lib/stores/filters";
import {
  getQuestionnaire,
  scoreQuestionnaire,
  suggestPortfolio,
  runBlackLitterman,
  getEfficientFrontier,
  type PortfolioBuildResult,
  type AdvisorBacktest,
  type SuggestResponse,
} from "@/lib/api/portfolioAdvisor";
import { CorrelationHeatmap } from "@/components/quant/CorrelationHeatmap";
import { getNavHistory } from "@/lib/api/schemes";
import { getMetaStatus } from "@/lib/api/meta";
import { runBacktest } from "@/lib/api/backtest";

const SECTION = "portfolio_advisor";

const PICK_TABLE_COLUMNS: ColumnConfig[] = [
  { key: "sleeve", label: "Sleeve" },
  { key: "scheme_name", label: "Fund" },
  { key: "fund_house", label: "AMC" },
  { key: "weight_pct", label: "Weight %", format: "signed_pct" },
  { key: "amount", label: "Amount", format: "inr" },
  { key: "expense_ratio", label: "TER %", format: "signed_pct" },
  { key: "ter_status", label: "TER status" },
  { key: "sharpe_ratio", label: "Sharpe", format: "number", decimals: 4 },
  { key: "sortino_ratio", label: "Sortino", format: "number", decimals: 4 },
  { key: "max_drawdown_pct", label: "Max DD %", format: "signed_pct" },
];

function AllocChart({ result, title }: { result: PortfolioBuildResult; title: string }) {
  const rows: { sleeve: string; weight: number; type: string }[] = [];
  if (result.target_alloc) {
    for (const [sleeve, w] of Object.entries(result.target_alloc)) {
      if (w > 0) rows.push({ sleeve, weight: Number((w * 100).toFixed(2)), type: "Target" });
    }
  }
  const actualBySleeve = new Map<string, number>();
  for (const pick of result.picks ?? []) {
    if (pick.scheme_code) actualBySleeve.set(pick.sleeve, (actualBySleeve.get(pick.sleeve) ?? 0) + (pick.weight_pct ?? 0));
  }
  for (const [sleeve, w] of actualBySleeve) rows.push({ sleeve, weight: Number(w.toFixed(2)), type: "Actual (Suggested)" });
  if (rows.length === 0) return null;

  const sleeves = Array.from(new Set(rows.map((r) => r.sleeve)));
  const figure = {
    data: ["Target", "Actual (Suggested)"].map((type) => ({
      type: "bar",
      name: type,
      x: sleeves,
      y: sleeves.map((s) => rows.find((r) => r.sleeve === s && r.type === type)?.weight ?? 0),
    })),
    layout: { title: { text: title }, barmode: "group", yaxis: { ticksuffix: "%" }, height: 380 },
  };
  return <PlotlyChart figure={figure} />;
}

function PicksTable({ result }: { result: PortfolioBuildResult }) {
  const rows = (result.picks ?? []).filter((p) => p.scheme_code);
  const errors = (result.picks ?? []).filter((p) => !p.scheme_code && p.error);
  if (rows.length === 0) {
    return <Banner level="warning">No funds could be selected for this method in the current window.</Banner>;
  }
  return (
    <>
      {errors.map((p, i) => (
        <div key={i} className="mb-2">
          <Banner level="info">
            <b>{p.sleeve}:</b> {p.error}
          </Banner>
        </div>
      ))}
      <DataTable columns={PICK_TABLE_COLUMNS} rows={rows as never} keyField="scheme_code" />
      <div className="mt-3 flex flex-col gap-2">
        {rows.map((p) => (
          <details key={p.scheme_code} className="text-sm">
            <summary className="cursor-pointer font-semibold">Why {p.scheme_name}?</summary>
            <p className="mt-1" style={{ color: "var(--mf-muted)" }}>
              {p.why}
            </p>
          </details>
        ))}
      </div>
      {(result.folded_notes ?? []).map((note, i) => (
        <div key={i} className="mt-2">
          <Banner level="info">{note}</Banner>
        </div>
      ))}
    </>
  );
}

function BacktestView({ bt }: { bt: AdvisorBacktest | null }) {
  if (bt === null || bt.error) {
    return <Banner level="warning">{bt?.error ?? "Could not backtest this portfolio."}</Banner>;
  }
  const twr = bt.twr_metrics;
  const figure = {
    data: [
      { type: "scatter", mode: "lines", name: "Portfolio Value", x: (bt.df_result ?? []).map((r) => r.nav_date), y: (bt.df_result ?? []).map((r) => r.portfolio_value), line: { color: "#2563EB", width: 2.5 } },
      { type: "scatter", mode: "lines", name: "Capital Invested", x: (bt.df_result ?? []).map((r) => r.nav_date), y: (bt.df_result ?? []).map((r) => r.total_invested), line: { color: CHART_MUTED_COLOR, width: 1.5, dash: "dash" } },
    ],
    layout: { height: 400, hovermode: "x unified", hoversort: "value descending", yaxis: { showgrid: true }, legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "right", x: 1 } },
  };
  return (
    <>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard title="Final Value" value={formatInr(bt.final_value ?? 0)} sub={`Invested ${formatInr(bt.total_invested ?? 0)}`} />
        <StatCard title="XIRR (Money-Weighted)" value={bt.money_weighted_xirr_pct != null ? formatSignedPct(bt.money_weighted_xirr_pct) : "N/A"} sub="Your actual annualized return" tone={toneOf(bt.money_weighted_xirr_pct ?? null)} />
        <StatCard title="Time-Weighted CAGR" value={twr?.cagr_pct != null ? formatSignedPct(twr.cagr_pct) : "N/A"} sub="Strategy return, excludes timing" tone={toneOf(twr?.cagr_pct ?? null)} />
        <StatCard title="Sharpe (TWR)" value={twr?.sharpe_ratio != null ? twr.sharpe_ratio.toFixed(4) : "N/A"} sub="Risk-adjusted return" />
      </div>
      <div className="mt-4 grid grid-cols-2 gap-4">
        <StatCard title="Max Drawdown (TWR)" value={twr?.max_drawdown_pct != null ? `${twr.max_drawdown_pct.toFixed(4)}%` : "N/A"} sub="Deepest peak-to-trough decline" />
        <StatCard title="Volatility (TWR, Ann.)" value={twr?.vol_annualized_pct != null ? `${twr.vol_annualized_pct.toFixed(4)}%` : "N/A"} sub="Annualized standard deviation" />
      </div>
      <div className="mt-4">
        <PlotlyChart figure={figure} />
      </div>
    </>
  );
}

export default function PortfolioAdvisorPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm">Loading Portfolio Advisor...</div>}>
      <PortfolioAdvisorContent />
    </Suspense>
  );
}

function localISO(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function parseLocalDate(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, (m || 1) - 1, d || 1);
}

function defaultHoldout(dbMax: string) {
  const end = parseLocalDate(dbMax);
  const fitEnd = new Date(end);
  fitEnd.setFullYear(fitEnd.getFullYear() - 1);
  const fitStart = new Date(end);
  fitStart.setFullYear(fitStart.getFullYear() - 4);
  return { fitStart: localISO(fitStart), fitEnd: localISO(fitEnd), testStart: localISO(fitEnd), testEnd: localISO(end) };
}

function PortfolioAdvisorContent() {
  const searchParams = useSearchParams();
  const getFilter = useFilterStore((s) => s.getFilter);
  const setFilter = useFilterStore((s) => s.setFilter);
  const { data: meta } = useQuery({ queryKey: ["meta-status"], queryFn: getMetaStatus });

  const { data: questionnaireData } = useQuery({ queryKey: ["padv-questionnaire"], queryFn: getQuestionnaire });

  const paramTier = searchParams.get("tier");
  const paramMode = searchParams.get("mode") as "Lump Sum" | "SIP (Monthly)" | null;
  const paramTab = searchParams.get("tab") as "rules" | "mvo" | "hrp" | "bl" | "compare" | null;
  const paramLumpSum = searchParams.get("lump_sum");
  const paramSipAmount = searchParams.get("sip_amount");
  const paramHorizon = searchParams.get("horizon");

  const [profileMode, setProfileModeState] = useState<"questionnaire" | "quick">(() => (getFilter(SECTION, "profile_mode", "Questionnaire") === "Questionnaire" ? "questionnaire" : "quick"));
  const [answers, setAnswersState] = useState<Record<string, number>>({});
  const [computedTier, setComputedTier] = useState<string | null>(null);
  const [computedScore, setComputedScore] = useState<number | null>(null);
  const [quickTier, setQuickTierState] = useState<string>(() => (paramTier ? paramTier : getFilter(SECTION, "quick_tier", "Balanced")));
  const [invMode, setInvModeState] = useState<"Lump Sum" | "SIP (Monthly)">(
    () => (paramMode === "Lump Sum" || paramMode === "SIP (Monthly)" ? paramMode : getFilter(SECTION, "invest_mode", "Lump Sum"))
  );
  const [lumpSum, setLumpSumState] = useState<number>(() =>
    paramLumpSum && !Number.isNaN(Number(paramLumpSum)) ? Number(paramLumpSum) : getFilter(SECTION, "lump_sum_amount", 100000)
  );
  const [sipAmount, setSipAmountState] = useState<number>(() =>
    paramSipAmount && !Number.isNaN(Number(paramSipAmount)) ? Number(paramSipAmount) : getFilter(SECTION, "sip_amount", 5000)
  );
  const [horizonYears, setHorizonYearsState] = useState<number>(() =>
    paramHorizon && !Number.isNaN(Number(paramHorizon)) ? Number(paramHorizon) : getFilter(SECTION, "horizon_years", 5)
  );
  const [generated, setGenerated] = useState(false);
  const [suitabilityWarning, setSuitabilityWarning] = useState<string | null>(null);
  const [blViews, setBlViews] = useState<{ kind: "absolute" | "relative"; i: number; j: number; qPct: number; conf: number }[]>([]);
  const [fitStart, setFitStart] = useState(() => searchParams.get("fit_start") || "");
  const [fitEnd, setFitEnd] = useState(() => searchParams.get("fit_end") || "");
  const [testStart, setTestStart] = useState(() => searchParams.get("test_start") || "");
  const [testEnd, setTestEnd] = useState(() => searchParams.get("test_end") || "");
  const urlHasHoldout =
    Boolean(searchParams.get("fit_start") || searchParams.get("fit_end") || searchParams.get("test_start") || searchParams.get("test_end"));

  useEffect(() => {
    if (!meta?.max_date || urlHasHoldout) return;
    if (fitStart && fitEnd && testStart && testEnd) return;
    const d = defaultHoldout(meta.max_date);
    setFitStart(d.fitStart);
    setFitEnd(d.fitEnd);
    setTestStart(d.testStart);
    setTestEnd(d.testEnd);
  }, [meta?.max_date, urlHasHoldout, fitStart, fitEnd, testStart, testEnd]);
  const [activeTab, setActiveTab] = useState<"rules" | "mvo" | "hrp" | "bl" | "compare">(() =>
    paramTab === "rules" || paramTab === "mvo" || paramTab === "hrp" || paramTab === "compare" || paramTab === "bl" ? paramTab : "rules"
  );

  // Synchronize advisor view state with URL parameters for sharing & bookmarking
  useUrlSync({
    tier: quickTier !== "Balanced" ? quickTier : undefined,
    mode: invMode !== "Lump Sum" ? invMode : undefined,
    tab: activeTab !== "rules" ? activeTab : undefined,
    lump_sum: invMode === "Lump Sum" && lumpSum !== 100000 ? lumpSum : undefined,
    sip_amount: invMode === "SIP (Monthly)" && sipAmount !== 5000 ? sipAmount : undefined,
    horizon: horizonYears !== 5 ? horizonYears : undefined,
    fit_start: fitStart || undefined,
    fit_end: fitEnd || undefined,
    test_start: testStart || undefined,
    test_end: testEnd || undefined,
  });

  function setProfileMode(v: "questionnaire" | "quick") {
    setProfileModeState(v);
    setFilter(SECTION, "profile_mode", v === "questionnaire" ? "Questionnaire" : "QuickSelect");
  }
  function setQuickTier(v: string) {
    setQuickTierState(v);
    setFilter(SECTION, "quick_tier", v);
  }
  function setInvMode(v: "Lump Sum" | "SIP (Monthly)") {
    setInvModeState(v);
    setFilter(SECTION, "invest_mode", v);
  }
  function setLumpSum(v: number) {
    setLumpSumState(v);
    setFilter(SECTION, "lump_sum_amount", v);
  }
  function setSipAmount(v: number) {
    setSipAmountState(v);
    setFilter(SECTION, "sip_amount", v);
  }
  function setHorizonYears(v: number) {
    setHorizonYearsState(v);
    setFilter(SECTION, "horizon_years", v);
  }

  function answerQuestion(key: string, value: number) {
    setAnswersState((prev) => ({ ...prev, [key]: value }));
  }

  const requiredKeys = (questionnaireData?.questions ?? []).filter((q) => q.key !== "age_bracket").map((q) => q.key);
  const canScore = requiredKeys.every((k) => answers[k] !== undefined);

  async function submitQuestionnaire() {
    const result = await scoreQuestionnaire(answers, horizonYears);
    setComputedTier(result.risk_tier);
    setComputedScore(result.score);
    setSuitabilityWarning(result.suitability_warning ?? null);
  }

  const riskTier = profileMode === "questionnaire" ? computedTier : quickTier;
  const budget = invMode === "Lump Sum" ? lumpSum : sipAmount;

  const { data: suggestResult, isFetching: suggestLoading } = useQuery({
    queryKey: ["portfolio-suggest", riskTier, budget, invMode, lumpSum, sipAmount, fitStart, fitEnd, testStart, testEnd],
    queryFn: () =>
      suggestPortfolio({
        risk_tier: riskTier!,
        budget,
        mode: invMode,
        lump_sum_amount: invMode === "Lump Sum" ? lumpSum : 0,
        sip_amount: invMode === "SIP (Monthly)" ? sipAmount : 0,
        start_date: fitStart,
        end_date: testEnd,
        construction_start: fitStart,
        construction_end: fitEnd,
        test_start: testStart,
        test_end: testEnd,
        holdout: true,
      }),
    enabled: generated && !!riskTier && !!fitStart && !!testEnd,
  });

  const eqTotal = riskTier && questionnaireData ? (questionnaireData.sleeve_allocations[riskTier]?.["Equity Core"] ?? 0) + (questionnaireData.sleeve_allocations[riskTier]?.["Equity Satellite"] ?? 0) + (questionnaireData.sleeve_allocations[riskTier]?.["International Equity"] ?? 0) : 0;

  const pickCodes = useMemo(() => {
    if (!suggestResult?.rules_result?.picks) return [];
    return suggestResult.rules_result.picks
      .map((p) => p.scheme_code)
      .filter((c): c is number => typeof c === "number" && !Number.isNaN(c));
  }, [suggestResult]);

  const { data: navHistoryData } = useQuery({
    queryKey: ["portfolio-nav-history", pickCodes, fitStart, fitEnd],
    queryFn: () => getNavHistory(pickCodes, fitStart, fitEnd),
    enabled: pickCodes.length > 1 && !!fitStart && !!fitEnd,
  });

  const { corrMatrix, corrAssetNames } = useMemo(() => {
    const hrp = suggestResult?.hrp_result as { correlation_matrix?: number[][]; asset_names?: string[]; picks?: { scheme_name?: string; scheme_code?: number }[] } | undefined;
    if (hrp?.correlation_matrix && Array.isArray(hrp.correlation_matrix)) {
      const names = hrp.asset_names ?? (hrp.picks ?? []).map((p: any) => p.scheme_name || String(p.scheme_code));
      return { corrMatrix: hrp.correlation_matrix, corrAssetNames: names };
    }

    if (!navHistoryData || navHistoryData.length === 0 || pickCodes.length === 0) {
      return { corrMatrix: [] as number[][], corrAssetNames: [] as string[] };
    }

    const codeToName = new Map<number, string>();
    for (const p of suggestResult?.rules_result?.picks ?? []) {
      if (p.scheme_code) codeToName.set(p.scheme_code, p.scheme_name ?? String(p.scheme_code));
    }

    const navByCode = new Map<number, Map<string, number>>();
    for (const pt of navHistoryData) {
      if (!navByCode.has(pt.scheme_code)) navByCode.set(pt.scheme_code, new Map());
      navByCode.get(pt.scheme_code)!.set(pt.nav_date, pt.nav);
    }

    const codes = Array.from(navByCode.keys());
    if (codes.length < 2) return { corrMatrix: [], corrAssetNames: [] };

    const allDates = Array.from(new Set(navHistoryData.map((d) => d.nav_date))).sort();
    const retsByCode = new Map<number, number[]>();

    for (const c of codes) {
      const priceMap = navByCode.get(c)!;
      const rets: number[] = [];
      let prevNav: number | null = null;
      for (const d of allDates) {
        const nav = priceMap.get(d);
        if (nav !== undefined && prevNav !== null && prevNav > 0) {
          rets.push((nav - prevNav) / prevNav);
        } else {
          rets.push(0);
        }
        if (nav !== undefined) prevNav = nav;
      }
      retsByCode.set(c, rets);
    }

    const n = codes.length;
    const matrix: number[][] = Array.from({ length: n }, () => Array(n).fill(1.0));
    for (let i = 0; i < n; i++) {
      const r1 = retsByCode.get(codes[i])!;
      const mean1 = r1.reduce((a, b) => a + b, 0) / r1.length;
      const std1 = Math.sqrt(r1.reduce((a, b) => a + (b - mean1) ** 2, 0) / r1.length);

      for (let j = i + 1; j < n; j++) {
        const r2 = retsByCode.get(codes[j])!;
        const mean2 = r2.reduce((a, b) => a + b, 0) / r2.length;
        const std2 = Math.sqrt(r2.reduce((a, b) => a + (b - mean2) ** 2, 0) / r2.length);

        let cov = 0;
        for (let k = 0; k < r1.length; k++) {
          cov += (r1[k] - mean1) * (r2[k] - mean2);
        }
        cov /= r1.length;
        const corr = std1 > 0 && std2 > 0 ? Math.max(-1.0, Math.min(1.0, cov / (std1 * std2))) : 0;
        matrix[i][j] = Number(corr.toFixed(4));
        matrix[j][i] = matrix[i][j];
      }
    }

    const assetNames = codes.map((c) => codeToName.get(c) || String(c));
    return { corrMatrix: matrix, corrAssetNames: assetNames };
  }, [suggestResult, navHistoryData, pickCodes]);

  const clusterOrderNames = useMemo(() => {
    const hrpOrder = (suggestResult?.hrp_result as { cluster_order?: (string | number)[] } | undefined)?.cluster_order;
    if (!hrpOrder || hrpOrder.length === 0) return undefined;
    const codeToName = new Map<string, string>();
    for (const p of suggestResult?.rules_result?.picks ?? []) {
      if (p.scheme_code) {
        codeToName.set(String(p.scheme_code), p.scheme_name ?? String(p.scheme_code));
      }
    }
    return hrpOrder.map((c) => codeToName.get(String(c)) || String(c));
  }, [suggestResult]);

  const compareRows = useMemo(() => {
    if (!suggestResult) return [];
    const rows: { method: string; xirr: number | null; cagr: number | null; sharpe: number | null; vol: number | null; mdd: number | null }[] = [];
    for (const [label, bt] of [
      ["Rules-Based", suggestResult.rules_backtest],
      ["Efficient-Frontier", suggestResult.mvo_backtest],
      ["Hierarchical Risk Parity", suggestResult.hrp_backtest],
    ] as const) {
      if (bt && !bt.error) {
        rows.push({
          method: label,
          xirr: bt.money_weighted_xirr_pct ?? null,
          cagr: bt.twr_metrics?.cagr_pct ?? null,
          sharpe: bt.twr_metrics?.sharpe_ratio ?? null,
          vol: bt.twr_metrics?.vol_annualized_pct ?? null,
          mdd: bt.twr_metrics?.max_drawdown_pct ?? null,
        });
      }
    }
    return rows;
  }, [suggestResult]);

  const blUi = meta?.flags?.bl_ui === true;
  const sampleLabel = suggestResult?.windows?.sample === "oos" ? "realized holdout" : "realized in-sample";
  const { data: frontier } = useQuery({
    queryKey: ["efficient-frontier", riskTier, fitStart, fitEnd],
    queryFn: () => getEfficientFrontier({ risk_tier: riskTier!, construction_start: fitStart, construction_end: fitEnd }),
    enabled: generated && !!riskTier && activeTab === "compare",
  });
  const rulesWeights = suggestResult?.rules_result?.weights ?? {};
  const blCodes = Object.keys(rulesWeights).map(Number);
  const blPQ = useMemo(() => {
    const n = blCodes.length;
    const P: number[][] = [];
    const Q: number[] = [];
    const C: number[] = [];
    for (const v of blViews.slice(0, 4)) {
      if (v.i < 0 || v.i >= n) continue;
      const row = Array(n).fill(0);
      if (v.kind === "absolute") {
        row[v.i] = 1;
      } else {
        if (v.j < 0 || v.j >= n || v.j === v.i) continue;
        row[v.i] = 1;
        row[v.j] = -1;
      }
      P.push(row);
      Q.push(v.qPct / 100);
      C.push(Math.min(0.9, Math.max(0.1, v.conf / 100)));
    }
    return { P, Q, C };
  }, [blCodes, blViews]);
  const { data: blResult } = useQuery({
    queryKey: ["black-litterman", blCodes, fitStart, fitEnd, blPQ],
    queryFn: () =>
      runBlackLitterman({
        scheme_codes: blCodes,
        prior_weights: blCodes.map((c) => Number(rulesWeights[c] ?? rulesWeights[String(c)] ?? 0)),
        views_matrix_P: blPQ.P,
        views_returns_Q: blPQ.Q,
        views_confidences: blPQ.P.length ? blPQ.C : undefined,
        start_date: fitStart,
        end_date: fitEnd,
      }),
    enabled: blUi && generated && (activeTab === "bl" || activeTab === "compare") && blCodes.length >= 2,
  });
  const testWindowStart = suggestResult?.windows?.test_start_effective ?? testStart;
  const testWindowEnd = suggestResult?.windows?.test_end ?? testEnd;
  const { data: blBacktest } = useQuery({
    queryKey: ["bl-backtest", blResult, testWindowStart, testWindowEnd, invMode],
    queryFn: () => {
      const names = (blResult?.asset_names as string[]) ?? blCodes.map(String);
      const w = (blResult?.optimal_weights as number[]) ?? [];
      const weights: Record<number, number> = {};
      names.forEach((n, i) => {
        weights[Number(n)] = w[i];
      });
      return runBacktest({
        scheme_codes: names.map(Number),
        weights,
        mode: invMode,
        lump_sum_amount: invMode === "Lump Sum" ? lumpSum : 0,
        sip_amount: invMode === "SIP (Monthly)" ? sipAmount : 0,
        rebalance_freq: "None",
        start_date: testWindowStart,
        end_date: testWindowEnd,
      });
    },
    enabled: blUi && !!blResult && activeTab === "compare",
  });

  const compareFigure = useMemo(
    () => ({
      data: compareRows.map((r) => ({
        type: "scatter",
        mode: "markers+text",
        name: r.method,
        x: [r.vol],
        y: [r.cagr],
        text: [r.method],
        textposition: "top center",
        marker: {
          size: 16,
          color: r.method === "Rules-Based" ? "#2563EB" : r.method === "Hierarchical Risk Parity" ? "#059669" : "#DC2626",
        },
      })),
      layout: { title: { text: "Holdout TWR vs vol (weights frozen at construction_end)" }, xaxis: { title: { text: "Annualized Volatility (TWR) %" }, ticksuffix: "%" }, yaxis: { title: { text: "Time-Weighted CAGR %" }, ticksuffix: "%" }, height: 420, showlegend: false },
    }),
    [compareRows]
  );

  return (
    <AppShell>
      <h1 className="mf-page-title">Portfolio Suggestion</h1>
      <p className="mf-page-caption">Pick a risk level and a budget; get a model portfolio built from disclosed, quantitative rules against official AMFI historical data.</p>

      <div className="mt-4">
        <NonAdviceDisclaimer />
      </div>
      <details className="mt-3 rounded-lg border p-3 text-sm" style={{ borderColor: "var(--mf-border)" }}>
        <summary className="cursor-pointer font-semibold">Methodology</summary>
        <ul className="mt-2 list-disc pl-5 text-xs" style={{ color: "var(--mf-muted)" }}>
          <li>SCORE_WEIGHTS: sharpe 0.30, sortino 0.25, alpha_vs_sleeve_median 0.25, max_drawdown 0.10, expense_ratio 0.10</li>
          <li>Growth-only (no distribution feed). Direct-only except Gold and the International ETF/index branch.</li>
          <li>Official TER only in the expense z-score; unknown/legacy_unverified are a neutral 0 z-score.</li>
          <li>Σ = sample daily covariance × 252, no shrinkage. Long-only, weights sum to 1. MVO vol ceiling by tier. HRP: correlation distance, single linkage, recursive bisection.</li>
          <li>ELSS stays in Equity Core; 3-year lock-in per allotment lot (SIP lots unlock FIFO).</li>
          <li>International = FoF Overseas or India-listed Nasdaq / S&amp;P 500 ETF (not FoF Domestic; Gold owns that keyword).</li>
          <li>AMC diversification via used_amcs. costs_model = nav_only_no_exit_load_no_stt_no_tax.</li>
          <li>Risk-free rate is hardcoded 6.5%. Headline TWR is NAV.</li>
        </ul>
      </details>

      {/* --- Step 1: Risk Profile --- */}
      <h2 className="mt-6 text-lg font-bold">1. Risk Profile</h2>
      <div className="mt-2 flex gap-1.5">
        {(["questionnaire", "quick"] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setProfileMode(m)}
            className="rounded-full px-3 py-1.5 text-xs font-semibold"
            style={{ background: profileMode === m ? "var(--mf-accent-bg)" : "transparent", color: profileMode === m ? "var(--mf-accent)" : "var(--mf-fg)" }}
          >
            {m === "questionnaire" ? "Take the Risk Questionnaire" : "I know my risk level"}
          </button>
        ))}
      </div>

      {profileMode === "questionnaire" ? (
        <div className="filter-box mt-3">
          <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
            Answer honestly -- this is the same style of behavioral risk-tolerance questionnaire real advisers use.
          </p>
          <div className="mt-3 flex flex-col gap-4">
            {(questionnaireData?.questions ?? []).map((q) => (
              <div key={q.key}>
                <div className="text-sm font-semibold" style={{ color: "var(--mf-fg)" }}>{q.question}</div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {q.options.map(([label, value]) => (
                    <button
                      key={label}
                      type="button"
                      onClick={() => answerQuestion(q.key, value)}
                      className="rounded-full border px-3 py-1 text-xs font-medium transition-colors"
                      style={{
                        borderColor: answers[q.key] === value ? "var(--mf-accent)" : "var(--mf-border)",
                        background: answers[q.key] === value ? "var(--mf-accent-bg)" : "transparent",
                        color: answers[q.key] === value ? "var(--mf-accent)" : "var(--mf-fg)",
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <button
            type="button"
            disabled={!canScore}
            onClick={submitQuestionnaire}
            className="mt-4 rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-40"
            style={{ background: "var(--mf-accent)", color: "white" }}
          >
            Score My Risk Profile
          </button>
          {computedTier && (
            <div className="mt-3">
              <StatusPill label={`Your score: ${computedScore} -> ${computedTier}`} level="success" />
            </div>
          )}
          {suitabilityWarning && (
            <div className="mt-3">
              <Banner level="warning">{suitabilityWarning}</Banner>
            </div>
          )}
        </div>
      ) : (
        <div className="filter-box mt-3 max-w-xs">
          <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
            Risk Level
            <select
              value={quickTier}
              onChange={(e) => setQuickTier(e.target.value)}
              className="rounded-lg border px-2 py-1.5 text-sm"
              style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
            >
              {(questionnaireData?.risk_tiers ?? []).map((t) => (
                <option key={t} value={t} className="bg-white text-slate-900">
                  {t}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {riskTier && questionnaireData && (
        <p className="mt-2 text-sm" style={{ color: "var(--mf-muted)" }}>
          <b style={{ color: "var(--mf-fg)" }}>{riskTier}</b> targets ~<span className="font-semibold" style={{ color: "var(--mf-fg)" }}>{(eqTotal * 100).toFixed(0)}%</span> total equity, ~<span className="font-semibold" style={{ color: "var(--mf-fg)" }}>{((questionnaireData.sleeve_allocations[riskTier]?.["Debt"] ?? 0) * 100).toFixed(0)}%</span> debt,{" "}
          <span className="font-semibold" style={{ color: "var(--mf-fg)" }}>{((questionnaireData.sleeve_allocations[riskTier]?.["Gold"] ?? 0) * 100).toFixed(0)}%</span> gold, <span className="font-semibold" style={{ color: "var(--mf-fg)" }}>{((questionnaireData.sleeve_allocations[riskTier]?.["Liquid Buffer"] ?? 0) * 100).toFixed(0)}%</span> liquid buffer.
        </p>
      )}

      {/* --- Step 2: Budget & Horizon --- */}
      <h2 className="mt-6 text-lg font-bold border-t pt-4" style={{ borderColor: "var(--mf-border)" }}>
        2. Budget & Horizon
      </h2>
      <p className="mt-1 text-xs" style={{ color: "var(--mf-muted)" }}>
        This page uses a 3Y construction / 1Y holdout window, not the global 90-day screener default.
      </p>
      <div className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-4">
        <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
          Construction start
          <input type="date" value={fitStart} onChange={(e) => setFitStart(e.target.value)} className="rounded-lg border px-2 py-1.5 text-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }} />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
          Construction end
          <input type="date" value={fitEnd} onChange={(e) => setFitEnd(e.target.value)} className="rounded-lg border px-2 py-1.5 text-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }} />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
          Test start
          <input type="date" value={testStart} onChange={(e) => setTestStart(e.target.value)} className="rounded-lg border px-2 py-1.5 text-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }} />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
          Test end
          <input type="date" value={testEnd} onChange={(e) => setTestEnd(e.target.value)} className="rounded-lg border px-2 py-1.5 text-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }} />
        </label>
      </div>
      <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-3">
        <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
          Investment Mode
          <select value={invMode} onChange={(e) => setInvMode(e.target.value as never)} className="rounded-lg border px-2 py-1.5 text-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}>
            <option className="bg-white text-slate-900">Lump Sum</option>
            <option className="bg-white text-slate-900">SIP (Monthly)</option>
          </select>
        </label>
        {invMode === "Lump Sum" ? (
          <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
            Lump Sum Amount
            <input type="number" min={1000} step={5000} value={lumpSum} onChange={(e) => setLumpSum(Number(e.target.value))} className="rounded-lg border px-2 py-1.5 text-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }} />
          </label>
        ) : (
          <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
            Monthly SIP Amount
            <input type="number" min={500} step={500} value={sipAmount} onChange={(e) => setSipAmount(Number(e.target.value))} className="rounded-lg border px-2 py-1.5 text-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }} />
          </label>
        )}
        <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
          Investment Horizon (Years)
          <input type="number" min={1} max={40} step={1} value={horizonYears} onChange={(e) => setHorizonYears(Number(e.target.value))} className="rounded-lg border px-2 py-1.5 text-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }} />
        </label>
      </div>

      {/* --- Step 3: Generate --- */}
      <div className="mt-6 border-t pt-4" style={{ borderColor: "var(--mf-border)" }}>
        {!riskTier ? (
          <Banner level="info">Complete Step 1 to see your risk tier before generating a portfolio.</Banner>
        ) : (
          <button type="button" onClick={() => setGenerated(true)} className="rounded-lg px-5 py-2.5 text-sm font-semibold" style={{ background: "var(--mf-accent)", color: "white" }}>
            Generate Suggested Portfolios
          </button>
        )}
      </div>

      {!generated || !riskTier ? null : suggestLoading ? (
        <p className="mt-6 text-sm" style={{ color: "var(--mf-muted)" }}>
          Screening funds and building your {riskTier} portfolio...
        </p>
      ) : suggestResult?.rules_result.error ? (
        <div className="mt-6">
          <Banner level="danger">{suggestResult.rules_result.error}</Banner>
        </div>
      ) : suggestResult ? (
        <>
          {(suggestResult.warnings ?? []).map((w, i) => (
            <div key={i} className="mt-3">
              <Banner level="warning">{w}</Banner>
            </div>
          ))}
          <div className="mt-3">
            <Banner level="info">
              Rebalancing is simulated frictionlessly (no transaction costs, exit loads, STT, or tax). costs_model = nav_only_no_exit_load_no_stt_no_tax.
            </Banner>
          </div>
          <div className="mt-6 flex gap-1.5 border-b pb-2" style={{ borderColor: "var(--mf-border)" }}>
            {(["rules", "mvo", "hrp", ...(blUi ? (["bl"] as const) : []), "compare"] as const).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setActiveTab(t)}
                className="rounded-full px-3 py-1.5 text-xs font-semibold"
                style={{ background: activeTab === t ? "var(--mf-accent-bg)" : "transparent", color: activeTab === t ? "var(--mf-accent)" : "var(--mf-fg)" }}
              >
                {t === "rules"
                  ? "Rules-Based Model Portfolio"
                  : t === "mvo"
                  ? "Efficient-Frontier Optimized"
                  : t === "hrp"
                  ? "Hierarchical Risk Parity (HRP)"
                  : t === "bl"
                  ? "Black–Litterman"
                  : "Compare All"}
              </button>
            ))}
          </div>

          {activeTab === "rules" && (
            <div className="mt-4">
              <p className="mf-page-caption">Strategic asset allocation by risk tier, then the top risk-adjusted-quality fund per sleeve.</p>
              <AllocChart result={suggestResult.rules_result} title="Target vs. Actual Allocation" />
              <div className="mt-3">
                <PicksTable result={suggestResult.rules_result} />
              </div>
              <h3 className="mt-4 text-base font-bold">Historical Backtest</h3>
              <div className="mt-2">
                <BacktestView bt={suggestResult.rules_backtest} />
              </div>
            </div>
          )}

          {activeTab === "mvo" && (
            <div className="mt-4">
              <div className="flex items-center gap-2">
                <p className="mf-page-caption">
                  Mean-variance optimization (max historical Sharpe, long-only) over the rules-based screen&apos;s own top candidates per sleeve.
                </p>
                <FormulaTooltip
                  align="right"
                  label="Markowitz Mean-Variance Optimization (MVO)"
                  formula="\max_{w} \; \frac{w^T \mu - R_f}{\sqrt{w^T \Sigma w}} \quad \text{s.t.} \quad \sum_{i=1}^n w_i = 1, \; w_i \ge 0, \; \sigma_p \le \sigma_{\text{ceiling}}"
                  description="Solves for the optimal portfolio asset weights vector on the Markowitz Efficient Frontier maximizing risk-adjusted return subject to long-only full investment and risk-tier volatility constraints."
                />
              </div>
              {suggestResult.mvo_result?.error ? (
                <Banner level="warning">{suggestResult.mvo_result.error}</Banner>
              ) : suggestResult.mvo_result ? (
                <>
                  <Banner level="info">
                    Historical realized return stands in for &quot;expected return&quot; here. Achieved annualized volatility:{" "}
                    <b>{(suggestResult.mvo_result.achieved_vol_pct ?? 0).toFixed(4)}%</b> (tier ceiling: ~{(suggestResult.mvo_result.vol_ceiling_pct ?? 0).toFixed(0)}%
                    {suggestResult.mvo_result.vol_ceiling_applied === false ? ", relaxed -- infeasible for this candidate set" : ""}).
                  </Banner>
                  <div className="mt-3">
                    <AllocChart result={suggestResult.mvo_result} title="Optimizer Allocation" />
                  </div>
                  <div className="mt-3">
                    <PicksTable result={suggestResult.mvo_result} />
                  </div>
                  <h3 className="mt-4 text-base font-bold">Historical Backtest</h3>
                  <div className="mt-2">
                    <BacktestView bt={suggestResult.mvo_backtest} />
                  </div>
                </>
              ) : null}
            </div>
          )}

          {activeTab === "hrp" && (
            <div className="mt-4">
              <div className="flex items-center gap-2">
                <p className="mf-page-caption">
                  Hierarchical Risk Parity (HRP) machine learning tree clustering & recursive inverse-variance bisection over candidate funds.
                </p>
                <FormulaTooltip
                  align="right"
                  label="Hierarchical Risk Parity (HRP)"
                  formula="D_{ij} = \sqrt{\frac{1 - \rho_{ij}}{2}}, \quad w_1 = \alpha w, \quad w_2 = (1-\alpha) w"
                  description="Overcomes Markowitz covariance matrix inversion collapse using single-linkage dendrogram tree clustering, quasi-diagonalization, and recursive bisection."
                />
              </div>
              {!suggestResult.hrp_result ? (
                <Banner level="info">HRP optimization requires at least 30 trading days of common history across candidate funds.</Banner>
              ) : (
                <>
                  <Banner level="info">
                    Allocated weights via hierarchical tree clustering and recursive bisection without covariance inversion.
                    Achieved annualized volatility: <b>{(suggestResult.hrp_result.achieved_vol_pct ?? 0).toFixed(4)}%</b>.
                    {(suggestResult.risk_budgeting as { hierarchical_risk_parity?: { effective_number_of_correlated_bets?: number; hhi?: number } } | undefined)?.hierarchical_risk_parity && (
                      <span className="ml-2 font-medium">
                        Effective Bets (ENCB): <b>{Number((suggestResult.risk_budgeting as { hierarchical_risk_parity: { effective_number_of_correlated_bets?: number; hhi?: number } }).hierarchical_risk_parity.effective_number_of_correlated_bets ?? 0).toFixed(2)}</b>
                        {" | "}HHI: <b>{Number((suggestResult.risk_budgeting as { hierarchical_risk_parity: { hhi?: number } }).hierarchical_risk_parity.hhi ?? 0).toFixed(4)}</b>
                      </span>
                    )}
                  </Banner>

                  {/* Asset Correlation Matrix Heatmap with HRP Quasi-Diagonalization */}
                  {corrMatrix.length > 0 && (
                    <div className="mt-4 rounded-xl border p-4 shadow-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
                      <h3 className="text-sm font-bold mb-2" style={{ color: "var(--mf-fg)" }}>
                        Asset Correlation Matrix (HRP Quasi-Diagonalized Order)
                      </h3>
                      <CorrelationHeatmap
                        corrMatrix={corrMatrix}
                        assetNames={corrAssetNames}
                        clusterOrder={clusterOrderNames}
                        title="Candidate Funds Correlation Matrix (HRP Tree-Clustered)"
                      />
                    </div>
                  )}

                  <div className="mt-4">
                    <AllocChart result={suggestResult.hrp_result} title="HRP Portfolio Allocation" />
                  </div>
                  <div className="mt-3">
                    <PicksTable result={suggestResult.hrp_result} />
                  </div>
                  {suggestResult.hrp_backtest && (
                    <>
                      <h3 className="mt-4 text-base font-bold">Historical Backtest</h3>
                      <div className="mt-2">
                        <BacktestView bt={suggestResult.hrp_backtest} />
                      </div>
                    </>
                  )}
                </>
              )}
            </div>
          )}

          {activeTab === "bl" && blUi && (
            <div className="mt-4 space-y-3">
              <p className="mf-page-caption">Black–Litterman views on the rules-based universe. Prior weights are the rules sleeve weights. Zero rows (K=0) leave the posterior equal to the prior. Not computed on /suggest.</p>
              {blCodes.length < 2 ? (
                <Banner level="info">Need at least two rules picks to run Black–Litterman.</Banner>
              ) : (
                <>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      disabled={blViews.length >= 4}
                      className="rounded-lg px-3 py-1.5 text-xs font-semibold disabled:opacity-40"
                      style={{ background: "var(--mf-accent)", color: "white" }}
                      onClick={() => setBlViews((v) => [...v, { kind: "absolute", i: 0, j: 1, qPct: 8, conf: 50 }])}
                    >
                      Add view
                    </button>
                    <span className="text-xs" style={{ color: "var(--mf-muted)" }}>{blViews.length}/4 · confidence 10–90% (Idzorek)</span>
                  </div>
                  {blViews.map((row, idx) => (
                    <div key={idx} className="grid grid-cols-2 gap-2 md:grid-cols-6 text-xs">
                      <select value={row.kind} onChange={(e) => setBlViews((v) => v.map((r, i) => i === idx ? { ...r, kind: e.target.value as "absolute" | "relative" } : r))} className="rounded border px-2 py-1" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}>
                        <option value="absolute">Absolute</option>
                        <option value="relative">Relative</option>
                      </select>
                      <select value={row.i} onChange={(e) => setBlViews((v) => v.map((r, i) => i === idx ? { ...r, i: Number(e.target.value) } : r))} className="rounded border px-2 py-1" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}>
                        {blCodes.map((c, i) => <option key={c} value={i}>{suggestResult?.rules_result?.picks?.find((p) => p.scheme_code === c)?.scheme_name ?? c}</option>)}
                      </select>
                      {row.kind === "relative" && (
                        <select value={row.j} onChange={(e) => setBlViews((v) => v.map((r, i) => i === idx ? { ...r, j: Number(e.target.value) } : r))} className="rounded border px-2 py-1" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}>
                          {blCodes.map((c, i) => <option key={c} value={i}>{suggestResult?.rules_result?.picks?.find((p) => p.scheme_code === c)?.scheme_name ?? c}</option>)}
                        </select>
                      )}
                      <label className="flex flex-col">Q %
                        <input type="number" step={0.5} value={row.qPct} onChange={(e) => setBlViews((v) => v.map((r, i) => i === idx ? { ...r, qPct: Number(e.target.value) } : r))} className="rounded border px-2 py-1" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }} />
                      </label>
                      <label className="flex flex-col">Conf %
                        <input type="number" min={10} max={90} value={row.conf} onChange={(e) => setBlViews((v) => v.map((r, i) => i === idx ? { ...r, conf: Number(e.target.value) } : r))} className="rounded border px-2 py-1" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }} />
                      </label>
                      <button type="button" className="text-xs" onClick={() => setBlViews((v) => v.filter((_, i) => i !== idx))}>Remove</button>
                    </div>
                  ))}
                  {blResult && (
                    <PlotlyChart
                      figure={{
                        data: [
                          { type: "bar", name: "Prior π", x: (blResult.asset_names as string[]) ?? blCodes.map(String), y: ((blResult.prior_returns as number[]) ?? []).map((x) => x * 100) },
                          { type: "bar", name: "Posterior E[R]", x: (blResult.asset_names as string[]) ?? blCodes.map(String), y: ((blResult.posterior_expected_returns as number[]) ?? []).map((x) => x * 100) },
                        ],
                        layout: { barmode: "group", height: 340, yaxis: { title: { text: "Expected return %" }, ticksuffix: "%" }, title: { text: "Prior vs posterior expected returns" } },
                      }}
                    />
                  )}
                </>
              )}
            </div>
          )}

          {activeTab === "compare" && (
            <div className="mt-4">
              {compareRows.length === 0 ? (
                <Banner level="info">The optimizer didn&apos;t produce a comparable portfolio for this window -- see the Efficient-Frontier tab for details.</Banner>
              ) : (
                <>
                  <DataTable
                    columns={[
                      { key: "method", label: "Method" },
                      { key: "xirr", label: "XIRR %", format: "signed_pct" },
                      { key: "cagr", label: "TWR CAGR %", format: "signed_pct" },
                      { key: "sharpe", label: "Sharpe", format: "number", decimals: 4 },
                      { key: "vol", label: "Volatility %", format: "signed_pct" },
                      { key: "mdd", label: "Max Drawdown %", format: "signed_pct" },
                    ]}
                    rows={compareRows}
                    keyField="method"
                  />
                  <p className="mb-2 text-xs" style={{ color: "var(--mf-muted)" }}>
                    Curve fitted on the construction window; dots are {sampleLabel} TWR/vol of frozen weights on the test window (or the same window if in-sample).
                  </p>
                  <div className="mt-3">
                    <PlotlyChart
                      figure={{
                        data: [
                          ...(((frontier?.frontier_points as { volatility?: number; expected_return?: number }[]) ?? []).length
                            ? [{
                                type: "scatter",
                                mode: "lines",
                                name: "IS frontier (construction)",
                                x: ((frontier!.frontier_points as { volatility: number }[]) ?? []).map((p) => (p.volatility ?? 0) * 100),
                                y: ((frontier!.frontier_points as { expected_return: number }[]) ?? []).map((p) => (p.expected_return ?? 0) * 100),
                                line: { color: "#94A3B8" },
                              }]
                            : []),
                          ...compareFigure.data,
                          ...(blBacktest?.twr_metrics
                            ? [{
                                type: "scatter",
                                mode: "markers+text",
                                name: "Black–Litterman",
                                x: [blBacktest.twr_metrics.vol_annualized_pct],
                                y: [blBacktest.twr_metrics.cagr_pct],
                                text: ["Black–Litterman"],
                                textposition: "top center",
                                marker: { size: 16, color: "#7C3AED" },
                              }]
                            : []),
                        ],
                        layout: compareFigure.layout,
                      }}
                    />
                  </div>
                </>
              )}
            </div>
          )}
        </>
      ) : null}
    </AppShell>
  );
}
