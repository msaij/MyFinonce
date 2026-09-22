"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { CorrelationHeatmap } from "@/components/quant/CorrelationHeatmap";
import { FactorWaterfallChart } from "@/components/quant/FactorWaterfallChart";
import { Banner } from "@/components/shared/Banner";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { StatCard } from "@/components/shared/StatCard";
import {
  getHoldingsFactors,
  getHoldingsRisk,
  getHoldingsStress,
  getMonteCarlo,
  type HoldingsRisk,
  type PortfolioKey,
  type Shocks,
} from "@/lib/api/holdings";
import { formatDate, formatInr, formatSignedPct, toneOf } from "@/lib/format";
import { useDebouncedValue } from "@/lib/hooks";
import { formatSignedInr } from "@/lib/holdings";
import { AXIS, LOSS, REFERENCE, SERIES_1, SERIES_2 } from "@/lib/holdingsChart";

const pct = (v: number | null | undefined, d = 2) => (v === null || v === undefined ? "-" : `${v.toFixed(d)}%`);
const num = (v: number | null | undefined, d = 2) => (v === null || v === undefined ? "-" : v.toFixed(d));

const DEFAULT_SHOCKS: Shocks = { market: -15, size: -5, value: 2, momentum: -8 };
const SHOCK_LABELS: Record<keyof Shocks, string> = { market: "Market", size: "Size (small vs large)", value: "Value vs growth", momentum: "Momentum" };

export function RiskPanel({ pid }: { pid: PortfolioKey }) {
  const riskQ = useQuery({ queryKey: ["holdings", "risk", pid], queryFn: () => getHoldingsRisk(pid) });
  const r = riskQ.data;

  if (riskQ.isLoading) return <div className="mt-4 text-sm" style={{ color: "var(--mf-muted)" }}>Measuring risk on your actual portfolio…</div>;
  if (riskQ.isError) return <div className="mt-4"><Banner level="danger">{(riskQ.error as Error).message}</Banner></div>;
  if (!r || r.empty) return null;

  return (
    <div className="mt-4 flex flex-col gap-8">
      {r.insufficient ? (
        <Banner level="info">{r.message}</Banner>
      ) : (
        <RealisedRisk r={r} />
      )}
      <FactorsSection pid={pid} />
      <StressSection pid={pid} />
      <MonteCarloSection pid={pid} />
    </div>
  );
}

function RealisedRisk({ r }: { r: HoldingsRisk }) {
  const m = r.metrics!;
  const rel = r.relative;
  const h = r.holdings;

  const ddFig = useMemo(() => {
    const dd = r.drawdown!;
    return {
      underwater: {
        data: [{ type: "scatter", mode: "lines", x: dd.dates, y: dd.drawdown_pct, fill: "tozeroy", line: { color: LOSS, width: 1.5 }, fillcolor: "rgba(185,28,28,0.15)", hovertemplate: "%{x}<br>%{y:.2f}% below peak<extra></extra>" }],
        layout: { height: 240, margin: { t: 10, l: 55 }, showlegend: false, yaxis: { ...AXIS, ticksuffix: "%" }, xaxis: { ...AXIS } },
      },
      vol: {
        data: [{ type: "scatter", mode: "lines", x: dd.dates, y: dd.rolling_vol_pct, line: { color: SERIES_1, width: 2 }, hovertemplate: "%{x}<br>30-day vol %{y:.1f}%<extra></extra>" }],
        layout: { height: 240, margin: { t: 10, l: 55 }, showlegend: false, yaxis: { ...AXIS, ticksuffix: "%", rangemode: "tozero" }, xaxis: { ...AXIS } },
      },
    };
  }, [r.drawdown]);

  const rcFig = useMemo(() => {
    const rows = [...(h?.risk_contributions ?? [])].reverse();
    const names = rows.map((x) => (x.scheme_name.length > 40 ? `${x.scheme_name.slice(0, 39)}…` : x.scheme_name));
    return {
      data: [
        { type: "bar", orientation: "h", name: "Share of money", y: names, x: rows.map((x) => x.weight_pct), marker: { color: SERIES_1 }, text: rows.map((x) => `${x.weight_pct.toFixed(1)}%`), textposition: "outside", cliponaxis: false, hovertemplate: "%{y}<br>Money %{x:.1f}%<extra></extra>" },
        { type: "bar", orientation: "h", name: "Share of risk", y: names, x: rows.map((x) => x.risk_pct), marker: { color: SERIES_2 }, text: rows.map((x) => `${x.risk_pct.toFixed(1)}%`), textposition: "outside", cliponaxis: false, hovertemplate: "%{y}<br>Risk %{x:.1f}%<extra></extra>" },
      ],
      layout: { height: Math.max(200, 60 + rows.length * 56), barmode: "group", bargap: 0.3, bargroupgap: 0.08, margin: { l: 10, r: 60, t: 30 }, legend: { orientation: "h", y: 1.12 }, yaxis: { automargin: true }, xaxis: { ...AXIS, ticksuffix: "%" } },
    };
  }, [h?.risk_contributions]);

  return (
    <section className="flex flex-col gap-4">
      <div>
        <h3 className="text-base font-bold">Realised risk</h3>
        <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
          From your portfolio&apos;s time-weighted daily returns, {formatDate(r.window!.start)} to {formatDate(r.window!.end)} ({r.window!.n_trading_days} trading days, up to 3 years). Risk-free rate {r.risk_free_pct ?? 6.5}%.
        </p>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
        <StatCard title="Volatility (ann.)" value={pct(m.vol_annualized_pct)} />
        <StatCard title="Sharpe" value={num(m.sharpe_ratio)} tooltip={<FormulaTooltip label="Sharpe ratio" formula="(mean daily return − rf) / σ × √252" />} />
        <StatCard title="Sortino" value={num(m.sortino_ratio)} />
        <StatCard title="Max drawdown" value={pct(m.max_drawdown_pct)} tone="neg" sub={`Now ${pct(m.current_drawdown_pct)}`} />
        <StatCard title="1-day VaR 95%" value={pct(m.var_95_daily_pct)} tone="neg" sub="Historical" tooltip={<FormulaTooltip label="Value at Risk" description="On 1 day in 20, your portfolio fell at least this much." />} />
        <StatCard title="CVaR 95% (ann.)" value={pct(m.cvar_95_ann_pct)} tone="neg" tooltip={<FormulaTooltip label="Expected shortfall" description="Average of the worst 5% of days, annualised. What a bad day looks like on average." />} />
        {rel && (
          <>
            <StatCard title={`Beta vs ${r.benchmark?.scheme_name ?? "benchmark"}`} value={num(rel.beta)} sub={`R² ${num(rel.r_squared)}`} />
            <StatCard title="Alpha (ann.)" value={formatSignedPct(rel.alpha_annualized_pct, 2)} tone={toneOf(rel.alpha_annualized_pct)} sub="Jensen's alpha" />
            <StatCard title="Up capture" value={pct(rel.up_market_capture_pct, 0)} />
            <StatCard title="Down capture" value={pct(rel.down_market_capture_pct, 0)} sub="Lower is better" />
            <StatCard title="Tracking error" value={pct(rel.tracking_error_pct)} />
            <StatCard title="Information ratio" value={num(rel.information_ratio)} />
          </>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div>
          <h4 className="text-sm font-bold">Drawdown from peak</h4>
          <PlotlyChart figure={ddFig.underwater} />
        </div>
        <div>
          <h4 className="text-sm font-bold">30-day rolling volatility (annualised)</h4>
          <PlotlyChart figure={ddFig.vol} />
        </div>
      </div>

      {h && !h.available && <Banner level="info">Fund-level risk: {h.reason}</Banner>}
      {h?.available && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            <StatCard title="Effective independent bets" value={num(h.effective_bets, 1)} sub={`vs ${num(h.effective_funds, 1)} effective funds by weight`} tooltip={<FormulaTooltip label="Effective number of correlated bets" description="Meucci's measure. Funds that move together count as one bet. Far fewer bets than funds means the diversification is mostly on paper." />} />
            <StatCard title="Portfolio volatility (ex-ante)" value={pct(h.portfolio_vol_pct)} sub={`Current weights, ${h.n_days} days of co-movement`} />
            <StatCard title="Funds compared" value={String(h.codes?.length ?? 0)} sub={h.excluded_short_history?.length ? `${h.excluded_short_history.length} excluded: too new` : `Since ${formatDate(h.window_start ?? null)}`} />
          </div>
          {(h.redundant_pairs ?? []).map((p) => (
            <Banner key={`${p.a}-${p.b}`} level="warning">
              <b>Possible overlap:</b> {p.a_name} and {p.b_name} are both {p.category} funds and moved together {(p.correlation * 100).toFixed(0)}% of the time (correlation {p.correlation.toFixed(2)}). Together they may add cost and complexity without adding diversification.
            </Banner>
          ))}
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <div>
              <h4 className="flex items-center gap-1 text-sm font-bold">
                Money vs risk, by fund
                <FormulaTooltip label="Risk contribution" formula="RCᵢ = wᵢ (Σw)ᵢ / σₚ" description="Each fund's share of the portfolio's volatility. Shares add up to 100%. A fund whose risk bar is much longer than its money bar is where your risk really sits." />
              </h4>
              <PlotlyChart figure={rcFig} />
            </div>
            <div>
              <h4 className="text-sm font-bold">How your funds move together</h4>
              <CorrelationHeatmap corrMatrix={h.correlation ?? []} assetNames={(h.names ?? []).map((n) => (n.length > 28 ? `${n.slice(0, 27)}…` : n))} title="" />
            </div>
          </div>
        </>
      )}
    </section>
  );
}

function FactorsSection({ pid }: { pid: PortfolioKey }) {
  const { data, isLoading } = useQuery({ queryKey: ["holdings", "factors", pid], queryFn: () => getHoldingsFactors(pid) });
  if (isLoading || !data || data.empty) return null;
  return (
    <section className="flex flex-col gap-3">
      <h3 className="flex items-center gap-1 text-base font-bold">
        Style exposure (4-factor)
        <FormulaTooltip label="Factor model" formula="R − rf = α + β_mkt·MKT + β_smb·SMB + β_hml·HML + β_wml·WML + ε" description="The same Indian-market factor model as the Quant page, run on your whole portfolio. SMB/HML are built from category-average Direct-Growth fund returns." />
      </h3>
      {data.unavailable ? (
        <Banner level="info">{data.reason}</Banner>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
            {Object.entries(data.regression!.factor_betas).map(([k, v]) => (
              <StatCard key={k} title={`${k[0].toUpperCase()}${k.slice(1)} beta`} value={num(v)} sub={`p = ${num(data.regression!.p_values[k], 3)}`} />
            ))}
            <StatCard title="Alpha (ann.)" value={formatSignedPct(data.regression!.alpha_annualized_pct, 2)} tone={toneOf(data.regression!.alpha_annualized_pct)} />
            <StatCard title="Explained by factors" value={pct(data.regression!.r_squared * 100, 0)} sub={`Systematic ${pct(data.regression!.systematic_risk_pct, 0)}`} />
          </div>
          <FactorWaterfallChart waterfallData={data.regression!.waterfall_data} title="Where your annual excess return came from" />
        </>
      )}
    </section>
  );
}

function StressSection({ pid }: { pid: PortfolioKey }) {
  const [shocks, setShocks] = useState<Shocks>(DEFAULT_SHOCKS);
  const debounced = useDebouncedValue(shocks, 300);
  const { data } = useQuery({ queryKey: ["holdings", "stress", pid, debounced], queryFn: () => getHoldingsStress(pid, debounced), placeholderData: (prev) => prev });
  if (!data || data.empty) return null;
  const p = data.parametric;
  return (
    <section className="flex flex-col gap-3">
      <div>
        <h3 className="text-base font-bold">Stress test: today&apos;s portfolio in past crises</h3>
        <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
          <b>Hypothetical.</b> Your current fund mix held at today&apos;s weights and replayed through each crisis. Rupee figures apply that fall to today&apos;s {formatInr(data.current_value ?? 0)}.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {(data.scenarios ?? []).map((s) => (
          <div key={s.id} className="metric-card">
            <div className="metric-title">{s.name}</div>
            {s.available ? (
              <>
                <div className="metric-value mf-neg">{formatSignedInr(s.rupee_impact)}</div>
                <div className="text-xs" style={{ color: "var(--mf-muted)" }}>
                  Fall {pct(s.drawdown_pct)} vs benchmark {pct(s.benchmark_drawdown_pct)}
                  <br />
                  {s.recovered ? `Recovered in ${s.recovery_days} days` : "Did not recover within the scan window"}
                  {s.coverage_pct < 99.5 && (
                    <>
                      <br />
                      <span style={{ color: "var(--mf-warning)" }}>⚠ Only {s.coverage_pct.toFixed(0)}% of today&apos;s money existed then</span>
                    </>
                  )}
                </div>
              </>
            ) : (
              <div className="text-xs" style={{ color: "var(--mf-muted)" }}>Not enough history: none of your current funds existed during this window.</div>
            )}
          </div>
        ))}
      </div>

      <div className="rounded-lg border p-3" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
        <div className="text-sm font-bold">What-if shock</div>
        {p?.available ? (
          <div className="mt-2 grid grid-cols-1 gap-4 md:grid-cols-[1fr_auto]">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {(Object.keys(SHOCK_LABELS) as (keyof Shocks)[]).map((k) => (
                <label key={k} className="flex flex-col gap-1 text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>
                  <span>
                    {SHOCK_LABELS[k]}: <b style={{ color: "var(--mf-fg)" }}>{shocks[k] > 0 ? "+" : ""}{shocks[k]}%</b>
                    {p.betas?.[k] !== undefined && <span> · your beta {p.betas[k].toFixed(2)}</span>}
                  </span>
                  <input type="range" min={-40} max={40} step={1} value={shocks[k]} onChange={(e) => setShocks((s) => ({ ...s, [k]: Number(e.target.value) }))} aria-label={`${SHOCK_LABELS[k]} shock percent`} />
                </label>
              ))}
            </div>
            <div className="min-w-[12rem] text-right">
              <div className="text-xs" style={{ color: "var(--mf-muted)" }}>Estimated impact</div>
              <div className={`text-2xl font-bold ${toneOf(p.rupee_impact ?? 0) === "neg" ? "mf-neg" : toneOf(p.rupee_impact ?? 0) === "pos" ? "mf-pos" : ""}`}>{formatSignedInr(p.rupee_impact)}</div>
              <div className="text-xs">{formatSignedPct(p.total_return_impact_pct ?? null, 2)} · leaves {formatInr(p.projected_capital)}</div>
              <button type="button" className="mt-2 text-xs font-semibold" style={{ color: "var(--mf-accent)" }} onClick={() => setShocks(DEFAULT_SHOCKS)}>
                Reset
              </button>
            </div>
          </div>
        ) : (
          <div className="mt-1 text-xs" style={{ color: "var(--mf-muted)" }}>Needs 60+ trading days of history and the factor proxy funds&apos; NAVs.</div>
        )}
      </div>
    </section>
  );
}

function MonteCarloSection({ pid }: { pid: PortfolioKey }) {
  const [years, setYears] = useState(1);
  const { data } = useQuery({ queryKey: ["holdings", "mc", pid, years], queryFn: () => getMonteCarlo(pid, years) });
  const fig = useMemo(() => {
    if (!data || data.empty || data.insufficient || !data.days) return null;
    const x = data.days.map((d) => +(d / 252).toFixed(3));
    const band = (lo: number[], hi: number[], color: string, name: string) => [
      { type: "scatter", mode: "lines", x, y: lo, line: { width: 0 }, hoverinfo: "skip", showlegend: false },
      { type: "scatter", mode: "lines", x, y: hi, fill: "tonexty", fillcolor: color, line: { width: 0 }, name, hoverinfo: "skip" },
    ];
    return {
      data: [
        ...band(data.p5!, data.p95!, "rgba(42,120,214,0.12)", "5th–95th percentile"),
        ...band(data.p25!, data.p75!, "rgba(42,120,214,0.25)", "25th–75th percentile"),
        { type: "scatter", mode: "lines", name: "Median", x, y: data.p50, line: { color: SERIES_1, width: 2 }, hovertemplate: "Year %{x:.1f}<br>Median ₹%{y:,.0f}<extra></extra>" },
        { type: "scatter", mode: "lines", name: "Today's value", x: [0, x[x.length - 1]], y: [data.initial_value, data.initial_value], line: { color: REFERENCE, width: 1, dash: "dot" }, hoverinfo: "skip" },
      ],
      layout: { height: 320, legend: { orientation: "h", y: 1.12 }, margin: { t: 30, l: 75 }, hovermode: "x", xaxis: { ...AXIS, title: { text: "Years from today" } }, yaxis: { ...AXIS, tickprefix: "₹", tickformat: ",.0f" } },
    };
  }, [data]);
  if (!data || data.empty) return null;
  return (
    <section className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1 text-base font-bold">
          Range of outcomes
          <FormulaTooltip label="Monte Carlo (GBM)" description="1,000 simulated paths using your current mix's own daily drift and volatility from up to the last 3 years. It shows spread, not a forecast. Past volatility does not bound future losses." />
        </h3>
        <div className="flex gap-1 text-xs font-semibold">
          {[1, 3, 5].map((y) => (
            <button key={y} type="button" onClick={() => setYears(y)} className="rounded-full px-3 py-1" style={{ background: years === y ? "var(--mf-accent)" : "transparent", color: years === y ? "#fff" : "var(--mf-fg)", border: "1px solid var(--mf-border)" }}>
              {y}Y
            </button>
          ))}
        </div>
      </div>
      {data.insufficient ? (
        <Banner level="info">Needs at least 60 trading days of history for the current mix.</Banner>
      ) : fig ? (
        <>
          <PlotlyChart figure={fig} />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatCard title="Median outcome" value={formatInr(data.median_terminal ?? null)} />
            <StatCard title="Bad case (5th pct)" value={formatInr(data.p5![data.p5!.length - 1])} tone="neg" />
            <StatCard title="Good case (95th pct)" value={formatInr(data.p95![data.p95!.length - 1])} tone="pos" />
            <StatCard title="Chance of a gain" value={data.prob_profit_pct !== undefined ? `${data.prob_profit_pct.toFixed(0)}%` : "-"} />
          </div>
        </>
      ) : null}
    </section>
  );
}
