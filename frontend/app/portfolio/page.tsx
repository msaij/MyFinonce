"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { AppShell } from "@/components/layout/AppShell";
import { StatCard } from "@/components/shared/StatCard";
import { DataTable, ColumnConfig } from "@/components/shared/DataTable";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { Banner } from "@/components/shared/Banner";
import { StatusPill } from "@/components/layout/StatusPill";
import { formatSignedPct, formatInr, toneOf } from "@/lib/format";
import { useDateRangeStore } from "@/lib/stores/dateRange";
import { useFilterStore } from "@/lib/stores/filters";
import { getQuestionnaire, scoreQuestionnaire, suggestPortfolio, type PortfolioBuildResult, type AdvisorBacktest } from "@/lib/api/portfolioAdvisor";

const SECTION = "portfolio_advisor";

const PICK_TABLE_COLUMNS: ColumnConfig[] = [
  { key: "sleeve", label: "Sleeve" },
  { key: "scheme_name", label: "Fund" },
  { key: "fund_house", label: "AMC" },
  { key: "weight_pct", label: "Weight %", format: "signed_pct" },
  { key: "amount", label: "Amount", format: "inr" },
  { key: "expense_ratio", label: "TER %", format: "signed_pct" },
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
      { type: "scatter", mode: "lines", name: "Capital Invested", x: (bt.df_result ?? []).map((r) => r.nav_date), y: (bt.df_result ?? []).map((r) => r.total_invested), line: { color: "#94A3B8", width: 1.5, dash: "dash" } },
    ],
    layout: { height: 400, hovermode: "x unified", yaxis: { showgrid: true }, legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "right", x: 1 } },
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
  const { start, end } = useDateRangeStore();
  const getFilter = useFilterStore((s) => s.getFilter);
  const setFilter = useFilterStore((s) => s.setFilter);

  const { data: questionnaireData } = useQuery({ queryKey: ["padv-questionnaire"], queryFn: getQuestionnaire });

  const [profileMode, setProfileModeState] = useState<"questionnaire" | "quick">(() => (getFilter(SECTION, "profile_mode", "Questionnaire") === "Questionnaire" ? "questionnaire" : "quick"));
  const [answers, setAnswersState] = useState<Record<string, number>>({});
  const [computedTier, setComputedTier] = useState<string | null>(null);
  const [computedScore, setComputedScore] = useState<number | null>(null);
  const [quickTier, setQuickTierState] = useState<string>(() => getFilter(SECTION, "quick_tier", "Balanced"));
  const [invMode, setInvModeState] = useState<"Lump Sum" | "SIP (Monthly)">(() => getFilter(SECTION, "invest_mode", "Lump Sum"));
  const [lumpSum, setLumpSumState] = useState<number>(() => getFilter(SECTION, "lump_sum_amount", 100000));
  const [sipAmount, setSipAmountState] = useState<number>(() => getFilter(SECTION, "sip_amount", 5000));
  const [horizonYears, setHorizonYearsState] = useState<number>(() => getFilter(SECTION, "horizon_years", 5));
  const [generated, setGenerated] = useState(false);
  const [activeTab, setActiveTab] = useState<"rules" | "mvo" | "compare">("rules");

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
  }

  const riskTier = profileMode === "questionnaire" ? computedTier : quickTier;
  const budget = invMode === "Lump Sum" ? lumpSum : sipAmount;

  const { data: suggestResult, isFetching: suggestLoading } = useQuery({
    queryKey: ["portfolio-suggest", riskTier, budget, invMode, lumpSum, sipAmount, start, end],
    queryFn: () =>
      suggestPortfolio({
        risk_tier: riskTier!,
        budget,
        mode: invMode,
        lump_sum_amount: invMode === "Lump Sum" ? lumpSum : 0,
        sip_amount: invMode === "SIP (Monthly)" ? sipAmount : 0,
        start_date: start,
        end_date: end,
      }),
    enabled: generated && !!riskTier && !!start && !!end,
  });

  const eqTotal = riskTier && questionnaireData ? (questionnaireData.sleeve_allocations[riskTier]?.["Equity Core"] ?? 0) + (questionnaireData.sleeve_allocations[riskTier]?.["Equity Satellite"] ?? 0) + (questionnaireData.sleeve_allocations[riskTier]?.["International Equity"] ?? 0) : 0;

  const compareRows = useMemo(() => {
    if (!suggestResult) return [];
    const rows: { method: string; xirr: number | null; cagr: number | null; sharpe: number | null; vol: number | null; mdd: number | null }[] = [];
    for (const [label, bt] of [
      ["Rules-Based", suggestResult.rules_backtest],
      ["Efficient-Frontier", suggestResult.mvo_backtest],
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
        marker: { size: 16, color: r.method === "Rules-Based" ? "#2563EB" : "#DC2626" },
      })),
      layout: { title: { text: "Risk vs. Return -- Both Constructed Portfolios" }, xaxis: { title: { text: "Annualized Volatility (TWR) %" }, ticksuffix: "%" }, yaxis: { title: { text: "Time-Weighted CAGR %" }, ticksuffix: "%" }, height: 420, showlegend: false },
    }),
    [compareRows]
  );

  return (
    <AppShell>
      <h1 className="mf-page-title">Portfolio Suggestion</h1>
      <p className="mf-page-caption">Pick a risk level and a budget; get a model portfolio built from disclosed, quantitative rules against official AMFI historical data.</p>

      <div className="mt-4">
        <Banner level="warning">
          <b>This is not personalized investment advice.</b> Indian Mutual Funds Dashboard is not a SEBI-registered Investment Adviser or Research
          Analyst. Every allocation and fund pick below is the output of a disclosed, inspectable rule or formula applied to official AMFI
          historical data -- never a personalized recommendation. Consult a SEBI-registered Investment Adviser before investing.
        </Banner>
      </div>

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
                <div className="text-sm font-semibold">{q.question}</div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {q.options.map(([label, value]) => (
                    <button
                      key={label}
                      type="button"
                      onClick={() => answerQuestion(q.key, value)}
                      className="rounded-full border px-3 py-1 text-xs"
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
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {riskTier && questionnaireData && (
        <p className="mt-2 text-sm" style={{ color: "var(--mf-muted)" }}>
          <b>{riskTier}</b> targets ~{(eqTotal * 100).toFixed(0)}% total equity, ~{((questionnaireData.sleeve_allocations[riskTier]?.["Debt"] ?? 0) * 100).toFixed(0)}% debt,{" "}
          {((questionnaireData.sleeve_allocations[riskTier]?.["Gold"] ?? 0) * 100).toFixed(0)}% gold, {((questionnaireData.sleeve_allocations[riskTier]?.["Liquid Buffer"] ?? 0) * 100).toFixed(0)}% liquid buffer.
        </p>
      )}

      {/* --- Step 2: Budget & Horizon --- */}
      <h2 className="mt-6 text-lg font-bold border-t pt-4" style={{ borderColor: "var(--mf-border)" }}>
        2. Budget & Horizon
      </h2>
      <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-3">
        <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
          Investment Mode
          <select value={invMode} onChange={(e) => setInvMode(e.target.value as never)} className="rounded-lg border px-2 py-1.5 text-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}>
            <option>Lump Sum</option>
            <option>SIP (Monthly)</option>
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
          <div className="mt-6 flex gap-1.5 border-b pb-2" style={{ borderColor: "var(--mf-border)" }}>
            {(["rules", "mvo", "compare"] as const).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setActiveTab(t)}
                className="rounded-full px-3 py-1.5 text-xs font-semibold"
                style={{ background: activeTab === t ? "var(--mf-accent-bg)" : "transparent", color: activeTab === t ? "var(--mf-accent)" : "var(--mf-fg)" }}
              >
                {t === "rules" ? "Rules-Based Model Portfolio" : t === "mvo" ? "Efficient-Frontier Optimized" : "Compare Both"}
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
              <p className="mf-page-caption">Mean-variance optimization (max historical Sharpe, long-only) over the rules-based screen's own top candidates per sleeve.</p>
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
                  <div className="mt-3">
                    <PlotlyChart figure={compareFigure} />
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
