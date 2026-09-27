"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { CorrelationHeatmap } from "@/components/quant/CorrelationHeatmap";
import { FactorWaterfallChart } from "@/components/quant/FactorWaterfallChart";
import { Banner } from "@/components/shared/Banner";
import { DataTable, type ColumnConfig } from "@/components/shared/DataTable";
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
  type Riskometer,
  type Shocks,
} from "@/lib/api/holdings";
import { formatDate, formatInr, formatSignedPct, toneOf } from "@/lib/format";
import { useDebouncedValue } from "@/lib/hooks";
import {
  MC_MAX_DAYS,
  MC_MIN_DAYS,
  clampHorizonDays,
  daysToMonths,
  formatSignedInr,
  horizonAxis,
  horizonLabel,
  monthsToDays,
} from "@/lib/holdings";
import { AXIS, LOSS, REFERENCE, SERIES_1, SERIES_2 } from "@/lib/holdingsChart";

const pct = (v: number | null | undefined, d = 2) => (v === null || v === undefined ? "-" : `${v.toFixed(d)}%`);
const num = (v: number | null | undefined, d = 2) => (v === null || v === undefined ? "-" : v.toFixed(d));

/** What each factor of the Indian 4-factor model measures, for the tiles' ⓘ. */
const FACTOR_ABOUT: Record<string, string> = {
  market: "How much your portfolio moves with the stock market as a whole (Nifty-style index minus the risk-free rate). 1 moves in step; near 0 barely notices the market.",
  size: "Tilt toward small companies (small-cap minus large-cap returns). Positive means your portfolio behaves more like small caps.",
  value: "Tilt toward cheap 'value' stocks over expensive 'growth' stocks. Positive leans value, negative leans growth.",
  momentum: "Tilt toward recent winners over recent losers. Positive means your funds tend to hold what has been rising.",
};

/** SEBI's six riskometer levels, lowest first, on one hue from light to dark: an ordinal
 *  scale, so darker reads as "more" without needing a legend to decode the order. */
const RISK_RAMP = ["#fdebd9", "#fbd0a8", "#f6ab6f", "#eb6834", "#c04b16", "#8a320b"];

function SubHeading({ title, about }: { title: string; about: string }) {
  return (
    <h4 className="-mb-1 flex items-center gap-1 text-sm font-bold">
      {title}
      <FormulaTooltip label={title} description={about} />
    </h4>
  );
}

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
      {/* First, and outside the history check: SEBI's label is the fund house's own
          official classification, needs no NAVs, and for a debt or liquid portfolio says
          more than volatility can -- volatility cannot see a credit event before it happens. */}
      {r.riskometer && <RiskometerSection meter={r.riskometer} />}
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

const RISKOMETER_COLUMNS: ColumnConfig[] = [
  { key: "scheme_name", label: "Fund" },
  {
    key: "level",
    label: "SEBI riskometer",
    sortValue: (row) => (row.rank as number | null) ?? 0,
    render: (row, v) => (v ? String(v) : <span style={{ color: "var(--mf-muted)" }}>Not published yet</span>),
  },
  { key: "weight_pct", label: "Share of your money", format: "pct", decimals: 1 },
  { key: "as_of", label: "As of", format: "date" },
];

function RiskometerSection({ meter }: { meter: Riskometer }) {
  const fig = useMemo(() => {
    if (!meter.available || !meter.distribution) return null;
    return {
      // One 100% bar, one segment per level in scale order. The legend doubles as the key
      // to the scale, so every level is listed even when you hold nothing at it.
      data: meter.distribution.map((d, i) => ({
        type: "bar",
        orientation: "h",
        name: d.level,
        y: ["Your money"],
        x: [d.weight_pct],
        marker: { color: RISK_RAMP[i], line: { color: "#ffffff", width: 2 } },
        text: [d.weight_pct >= 8 ? `${d.weight_pct.toFixed(0)}%` : ""],
        textposition: "inside",
        insidetextanchor: "middle",
        textfont: { color: i >= 3 ? "#ffffff" : "#1f2328" },
        hovertemplate: `<b>${d.level}</b><br>%{x:.1f}% of your money<extra></extra>`,
      })),
      layout: {
        barmode: "stack",
        height: 130,
        margin: { l: 10, r: 10, t: 6, b: 6 },
        xaxis: { visible: false, range: [0, 100], fixedrange: true },
        yaxis: { visible: false, fixedrange: true },
        legend: { orientation: "h", y: -0.35, traceorder: "normal" },
        showlegend: true,
      },
    };
  }, [meter]);

  return (
    <section className="flex flex-col gap-3">
      <h3 className="flex items-center gap-1 text-base font-bold">
        SEBI riskometer
        <FormulaTooltip
          label="SEBI riskometer"
          description="Every Indian mutual fund must publish one of six risk levels, from Low to Very High. The fund house sets it under SEBI's rules from what the fund holds today (credit quality, sensitivity to interest rates and liquidity for debt; market risk for equity) and reviews it every month. It is the one figure on this tab that is not inferred from past prices, so it can see risks that have not shown up in the NAV yet."
        />
      </h3>
      {!meter.available ? (
        <Banner level="info">{meter.reason ?? "Riskometer labels are not available for your funds yet."}</Banner>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <StatCard
              title="Where your money sits"
              value={meter.portfolio_level ?? "-"}
              sub={`Money-weighted: ${meter.weighted_rank?.toFixed(1)} on SEBI's scale of 1–6`}
              tooltip={
                <FormulaTooltip
                  label="Portfolio riskometer (summary)"
                  formula="Σ (share of money × level 1–6), nearest level"
                  description="Each fund's level scored from 1 (Low) to 6 (Very High), averaged by how much money is in it. SEBI rates funds, not portfolios, so this is a summary of your holdings, not an official label."
                />
              }
            />
            <StatCard
              title="Highest level you hold"
              value={meter.highest?.level ?? "-"}
              sub={meter.highest ? `${meter.highest.scheme_name} · ${meter.highest.weight_pct.toFixed(1)}% of your money` : ""}
              tooltip={
                <FormulaTooltip
                  label="Highest level held"
                  description="Your riskiest single fund by its own label. Shown separately because an average can hide it: a small holding at Very High disappears inside a mostly-Low portfolio."
                />
              }
            />
          </div>
          {fig && <PlotlyChart figure={fig} />}
          {(meter.coverage_pct ?? 100) < 99.5 && (
            <p className="text-xs" style={{ color: "var(--mf-warning)" }}>
              ⚠ {(100 - (meter.coverage_pct ?? 100)).toFixed(1)}% of your money is in funds AMFI has not published a riskometer for yet;
              the summary covers the rest.
            </p>
          )}
          <DataTable
            columns={RISKOMETER_COLUMNS}
            rows={(meter.funds ?? []) as unknown as Record<string, unknown>[]}
            keyField="scheme_code"
          />
          <p className="-mt-1 text-xs" style={{ color: "var(--mf-muted)" }}>
            From AMFI&apos;s fund-performance disclosure{meter.as_of ? `, as of ${formatDate(meter.as_of)}` : ""}. Refreshed nightly.
          </p>
        </>
      )}
    </section>
  );
}

function RealisedRisk({ r }: { r: HoldingsRisk }) {
  const m = r.metrics!;
  const rel = r.relative;
  const h = r.holdings;

  const obsPerYear = r.window?.obs_per_year ?? m.obs_per_year ?? 252;

  // On the hypothetical current-mix basis, the early part of both charts is replayed from
  // only the funds that existed then -- for a portfolio holding a fund launched last month,
  // two fifths of the x-axis can represent ~90% of today's money. The stress cards already
  // warn about this; these two charts are built from the same index and must say it too.
  const partial = useMemo(() => {
    const cov = r.drawdown?.coverage_pct;
    if (!cov?.length) return null;
    const known = cov.filter((c): c is number => c !== null);
    const min = known.length ? Math.min(...known) : 100;
    if (min >= 99.5) return null;
    const full = cov.findIndex((c) => c !== null && c >= 99.5);
    return { min, until: r.drawdown!.dates[full === -1 ? cov.length - 1 : full] };
  }, [r.drawdown]);

  const ddFig = useMemo(() => {
    const dd = r.drawdown!;
    // Shade, rather than a second line: coverage is a caveat about the series, and a
    // second y-series would invite reading it as a number to compare against.
    const shade = partial
      ? [{ type: "rect", xref: "x", yref: "paper", x0: dd.dates[0], x1: partial.until, y0: 0, y1: 1, fillcolor: "rgba(120,120,120,0.10)", line: { width: 0 }, layer: "below" }]
      : [];
    return {
      underwater: {
        data: [{ type: "scatter", mode: "lines", x: dd.dates, y: dd.drawdown_pct, fill: "tozeroy", line: { color: LOSS, width: 1.5 }, fillcolor: "rgba(185,28,28,0.15)", hovertemplate: "%{x}<br>%{y:.4f}% below peak<extra></extra>" }],
        layout: { height: 240, margin: { t: 10, l: 55 }, showlegend: false, shapes: shade, yaxis: { ...AXIS, ticksuffix: "%" }, xaxis: { ...AXIS } },
      },
      vol: {
        // Four decimals: a liquid-fund portfolio's volatility sits around 0.1-0.3%, which a
        // one-decimal hover rounded to a flat "0.1%" / "0.2%" across the whole chart.
        data: [{ type: "scatter", mode: "lines", x: dd.dates, y: dd.rolling_vol_pct, line: { color: SERIES_1, width: 2 }, hovertemplate: "%{x}<br>30-day vol %{y:.4f}%<extra></extra>" }],
        layout: { height: 240, margin: { t: 10, l: 55 }, showlegend: false, shapes: shade, yaxis: { ...AXIS, ticksuffix: "%", rangemode: "tozero" }, xaxis: { ...AXIS } },
      },
    };
  }, [r.drawdown, partial]);

  const rcFig = useMemo(() => {
    const rows = [...(h?.risk_contributions ?? [])].reverse();
    const names = rows.map((x) => x.scheme_name);
    return {
      data: [
        // "Share of money" is the grey reference series here exactly as on the Performance
        // tab's share-of-gain chart, so one colour means one thing across the page; only the
        // compared series carries direct labels.
        { type: "bar", orientation: "h", name: "Share of your money", y: names, x: rows.map((x) => x.weight_pct), marker: { color: REFERENCE, cornerradius: 4 }, customdata: rows.map((x) => [x.weight_pct, x.risk_pct]), hovertemplate: "<b>%{y}</b><br>Money %{customdata[0]:.1f}% · Risk %{customdata[1]:.1f}%<extra></extra>" },
        { type: "bar", orientation: "h", name: "Share of your risk", y: names, x: rows.map((x) => x.risk_pct), marker: { color: SERIES_2, cornerradius: 4 }, text: rows.map((x) => `${x.risk_pct.toFixed(1)}%`), textposition: "outside", cliponaxis: false, customdata: rows.map((x) => [x.weight_pct, x.risk_pct]), hovertemplate: "<b>%{y}</b><br>Money %{customdata[0]:.1f}% · Risk %{customdata[1]:.1f}%<extra></extra>" },
      ],
      layout: { height: Math.max(200, 60 + rows.length * 56), barmode: "group", bargap: 0.3, bargroupgap: 0.08, margin: { l: 10, r: 60, t: 30 }, legend: { orientation: "h", y: 1.12, traceorder: "reversed" }, yaxis: { automargin: true }, xaxis: { ...AXIS, ticksuffix: "%" } },
    };
  }, [h?.risk_contributions]);

  return (
    <section className="flex flex-col gap-4">
      <div>
        <h3 className="flex items-center gap-1 text-base font-bold">
          {r.basis === "current_mix" ? "Risk of today's mix (hypothetical)" : "Realised risk"}
          <FormulaTooltip
            label={r.basis === "current_mix" ? "Hypothetical basis" : "Realised basis"}
            description={
              r.basis === "current_mix"
                ? "Your portfolio is too young for its own track record to say much, so today's fund weights are held fixed and replayed over each fund's own past NAVs. It answers \"how risky is what I hold now?\", not \"how risky has my portfolio been?\". It switches to your real track record once that reaches 60 days."
                : "Measured on your portfolio's own time-weighted daily returns: what actually happened to the money you held, with the effect of when you added or withdrew money taken out."
            }
          />
        </h3>
        <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
          {r.basis === "current_mix" ? "From today's fund weights replayed over the funds' own daily NAVs" : "From your portfolio's time-weighted daily returns"},{" "}
          {formatDate(r.window!.start)} to {formatDate(r.window!.end)} ({r.window!.n_trading_days.toLocaleString("en-IN")} daily observations, about{" "}
          {Math.round(obsPerYear)} a year, up to 3 years; every annualised figure is scaled by that rate). Risk-free rate {r.risk_free_pct ?? 6.5}%.
          {r.benchmark?.scheme_name && ` Benchmark: ${r.benchmark.scheme_code ? r.benchmark.scheme_name : "each fund's SEBI category average"}.`}
        </p>
      </div>
      {r.basis_note && <Banner level="info">{r.basis_note}</Banner>}
      <SubHeading
        title="How much it moves"
        about="Size of the ups and downs, and how well the return has paid for them. All from the same daily series; annualised figures are scaled by that series' own observation rate."
      />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatCard
          title="Volatility (ann.)"
          value={pct(m.vol_annualized_pct, 4)}
          sub={`Daily ${pct(m.vol_daily_pct, 4)}`}
          tooltip={
            <FormulaTooltip
              label="Volatility"
              formula={`σ(daily returns) × √${Math.round(obsPerYear)}`}
              description="How widely the portfolio's daily returns swing around their average, scaled to a year. About two years in three should land within one volatility of the average return. It treats a surprise gain as risk just as much as a loss."
            />
          }
        />
        <StatCard
          title="Sharpe"
          value={num(m.sharpe_ratio)}
          tooltip={
            <FormulaTooltip
              label="Sharpe ratio"
              formula={`(mean daily return − rf) / σ × √${Math.round(obsPerYear)}`}
              description={`Return earned above the risk-free rate (${r.risk_free_pct ?? 6.5}%) per unit of volatility. Above 1 is good, above 2 very good. For a liquid or debt portfolio the excess over the risk-free rate is small and the ratio swings a lot with the rate assumed, so read it alongside the riskometer. ${
                obsPerYear > 300
                  ? `Annualised at ${Math.round(obsPerYear)} observations a year: funds that publish a NAV every calendar day (liquid, overnight) set the grid here, and the textbook √252 applies only to trading-day data.`
                  : `Annualised at ${Math.round(obsPerYear)} observations a year, the trading-day rate this series is priced at.`
              }`}
            />
          }
        />
        <StatCard
          title="Sortino"
          value={num(m.sortino_ratio)}
          sub={`Downside dev. ${pct(m.downside_dev_ann_pct, 4)}`}
          tooltip={
            <FormulaTooltip
              label="Sortino ratio"
              formula="(CAGR − rf) / downside deviation"
              description="Like Sharpe, but only days that fell short of the risk-free rate count as risk; upside surprises are not penalised. Much higher than Sharpe means the swings are mostly on the upside."
            />
          }
        />
        <StatCard
          title="Max drawdown"
          value={pct(m.max_drawdown_pct, 4)}
          tone="neg"
          sub={`Now ${pct(m.current_drawdown_pct, 4)} below peak`}
          tooltip={
            <FormulaTooltip
              label="Maximum drawdown"
              formula="min over t of ( Vₜ / max(V₀…Vₜ) − 1 )"
              description="The largest fall from a previous high to a later low over the window: the worst loss you would have sat through had you bought at the top. 'Now' is how far below its own high the portfolio sits today."
            />
          }
        />
        <StatCard
          title="1-day VaR 95%"
          value={pct(m.var_95_daily_pct, 4)}
          tone={toneOf(m.var_95_daily_pct)}
          sub="Historical, 5th percentile day"
          tooltip={
            <FormulaTooltip
              label="Value at Risk (95%, 1 day)"
              formula="5th percentile of daily returns"
              description="On 19 days out of 20 the portfolio did better than this. Positive means even the 5% worst days were gains, which is normal for liquid and debt funds that accrue interest every day."
            />
          }
        />
        {/* This tile showed cvar_95_ann_pct -- a one-day tail loss multiplied by √365 (~19x)
            -- under a description promising "what a bad day looks like". The daily figure is
            what the description means, and it pairs directly with the 1-day VaR beside it. */}
        <StatCard
          title="1-day CVaR 95%"
          value={pct(m.cvar_95_daily_pct, 4)}
          tone={toneOf(m.cvar_95_daily_pct)}
          sub="Average of the worst 5% of days"
          tooltip={
            <FormulaTooltip
              label="Expected shortfall (CVaR, 95%, 1 day)"
              formula="mean of daily returns ≤ VaR 95%"
              description="When a day is among the worst 5%, this is how it goes on average: VaR says where the bad tail starts, CVaR says how deep it runs. Positive means even the bad tail was a small gain."
            />
          }
        />
      </div>

      <SubHeading
        title="Day-to-day"
        about="What the individual days have looked like. Useful for judging whether the averages above hide rare but large moves."
      />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard
          title="Worst day"
          value={formatSignedPct(m.worst_day_pct ?? null, 4)}
          tone={toneOf(m.worst_day_pct ?? null)}
          tooltip={<FormulaTooltip label="Worst day" description="The single largest one-day fall in the window. A far larger worst day than the 1-day CVaR means the tail has one or two outliers the averages smooth over." />}
        />
        <StatCard
          title="Best day"
          value={formatSignedPct(m.best_day_pct ?? null, 4)}
          tone={toneOf(m.best_day_pct ?? null)}
          tooltip={<FormulaTooltip label="Best day" description="The single largest one-day gain in the window." />}
        />
        <StatCard
          title="Up days"
          value={m.win_rate_pct !== null && m.win_rate_pct !== undefined ? `${m.win_rate_pct.toFixed(1)}%` : "-"}
          sub="Share of days with a gain"
          tooltip={<FormulaTooltip label="Up days (win rate)" description="The share of days the portfolio rose. Liquid and money-market funds accrue interest daily, so they sit close to 100%; an equity portfolio typically sits a little above 50%." />}
        />
        <StatCard
          title="Calmar"
          value={num(m.calmar_ratio ?? null)}
          sub={`CAGR ${pct(m.cagr_pct, 2)}`}
          tooltip={<FormulaTooltip label="Calmar ratio" formula="CAGR / |max drawdown|" description="Annual growth earned per unit of the worst fall. It gets very large when drawdowns are tiny, as they are for liquid funds, so compare it only between portfolios of the same kind." />}
        />
      </div>

      {rel && (
        <>
          <SubHeading
            title={r.benchmark?.scheme_code ? `Versus ${r.benchmark.scheme_name}` : "Versus your funds' peer groups"}
            about={
              r.benchmark?.scheme_code
                ? "Compared day by day with the benchmark you chose for this portfolio."
                : "Compared day by day with a blend of each fund's own SEBI category: the average Direct-Growth fund in that category, weighted like your portfolio. It asks whether you picked well within each category, not whether the category itself was a good choice."
            }
          />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <StatCard
              title="Beta"
              value={num(rel.beta)}
              sub={`R² ${num(rel.r_squared)}`}
              tooltip={<FormulaTooltip label="Beta" formula="cov(you, benchmark) / var(benchmark)" description="How much the portfolio moves for each 1% the benchmark moves: 1 moves in step, below 1 is calmer, above 1 amplifies it. R² is how much of your movement the benchmark explains; when it is low, beta says little." />}
            />
            <StatCard
              title="Alpha (ann.)"
              value={formatSignedPct(rel.alpha_annualized_pct, 2)}
              tone={toneOf(rel.alpha_annualized_pct)}
              sub="Jensen's alpha"
              tooltip={<FormulaTooltip label="Jensen's alpha" formula="(R − rf) − β (R_bench − rf)" description="Annual return beyond what the benchmark and your beta to it would predict. Positive means the funds added return that the benchmark exposure alone does not explain." />}
            />
            <StatCard
              title="Up capture"
              value={pct(rel.up_market_capture_pct, 0)}
              tooltip={<FormulaTooltip label="Up-market capture" description="On days the benchmark rose, how much of that rise you caught. 100% keeps pace; above 100% outpaced it." />}
            />
            <StatCard
              title="Down capture"
              value={pct(rel.down_market_capture_pct, 0)}
              sub="Lower is better"
              tooltip={<FormulaTooltip label="Down-market capture" description="On days the benchmark fell, how much of that fall you took. Below 100% means you fell less. The best combination is up capture above down capture." />}
            />
            <StatCard
              title="Tracking error"
              value={pct(rel.tracking_error_pct, 4)}
              tooltip={<FormulaTooltip label="Tracking error" formula={`σ(you − benchmark) × √${Math.round(obsPerYear)}`} description="How far your daily returns stray from the benchmark's, annualised. Small means you behave much like it; large means your results depend on your own fund choices." />}
            />
            <StatCard
              title="Information ratio"
              value={num(rel.information_ratio)}
              tooltip={<FormulaTooltip label="Information ratio" formula="(your CAGR − benchmark CAGR) / tracking error" description="Extra return over the benchmark per unit of tracking error: whether straying from it was paid for. Above 0.5 is good." />}
            />
          </div>
        </>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div>
          <h4 className="flex items-center gap-1 text-sm font-bold">
            Drawdown from peak
            <FormulaTooltip
              label="Drawdown from peak"
              formula="Vₜ / max(V₀…Vₜ) − 1"
              description="At each date, how far the portfolio sat below its highest point so far. Zero means a new high; the deepest point of the red area is the max drawdown tile above; the width of each dip is how long recovery took."
            />
          </h4>
          <PlotlyChart figure={ddFig.underwater} />
        </div>
        <div>
          <h4 className="flex items-center gap-1 text-sm font-bold">
            30-day rolling volatility (annualised)
            <FormulaTooltip
              label="Rolling volatility"
              formula={`σ(last 30 daily returns) × √${Math.round(obsPerYear)}`}
              description="Volatility re-measured every day over the trailing 30 observations, so you can see calm and turbulent stretches rather than one average. Spikes line up with market stress."
            />
          </h4>
          <PlotlyChart figure={ddFig.vol} />
        </div>
      </div>
      {partial && (
        <p className="-mt-2 text-xs" style={{ color: "var(--mf-warning)" }}>
          ⚠ Shaded: before {formatDate(partial.until)} some of your current funds did not exist yet, so those dates represent as little as{" "}
          {partial.min.toFixed(1)}% of today&apos;s money, replayed from the funds that did.
        </p>
      )}

      {h && !h.available && <Banner level="info">Fund-level risk: {h.reason}</Banner>}
      {h?.available && (
        <>
          <SubHeading
            title="Diversification across your funds"
            about="How your funds behave together, from each fund's own NAVs over the window their histories overlap. Holding many funds diversifies only if they do not move alike."
          />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            <StatCard title="Effective independent bets" value={num(h.effective_bets, 1)} sub={`vs ${num(h.effective_funds, 1)} effective funds by weight`} tooltip={<FormulaTooltip label="Effective number of correlated bets" description="Meucci's measure. Funds that move together count as one bet. Far fewer bets than funds means the diversification is mostly on paper. The comparison figure counts funds by weight alone, ignoring how they move." />} />
            <StatCard
              title="Portfolio volatility (ex-ante)"
              value={pct(h.portfolio_vol_pct, 4)}
              sub={`Current weights, ${h.n_days} days of co-movement`}
              tooltip={<FormulaTooltip label="Ex-ante volatility" formula="√(wᵀ Σ w)" description="The volatility today's weights imply, from how each pair of funds has moved together. It differs from the Volatility tile above, which is measured on the replayed series and covers a longer window; a gap between them usually means the funds' relationships have shifted." />}
            />
            <StatCard
              title="Funds compared"
              value={String(h.codes?.length ?? 0)}
              sub={h.excluded_short_history?.length ? `${h.excluded_short_history.length} excluded: too new` : `Since ${formatDate(h.window_start ?? null)}`}
              tooltip={<FormulaTooltip label="Funds compared" description="Funds need a shared stretch of history to be compared. A fund launched too recently is left out here rather than shrinking every other fund's window, so the shares in the chart below are of the funds compared, not of your whole portfolio." />}
            />
          </div>
          {(h.redundant_pairs ?? []).map((p) => (
            // Correlation is not a frequency: 0.98 does not mean "moved together 98% of the
            // time", it means their daily returns rise and fall almost in lockstep.
            <Banner key={`${p.a}-${p.b}`} level="warning">
              <b>Possible overlap:</b> {p.a_name} and {p.b_name} are both {p.category} funds and their daily returns move almost in lockstep (correlation {p.correlation.toFixed(2)}, where 1 is identical). Together they may add cost and complexity without adding diversification.
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
              <h4 className="flex items-center gap-1 text-sm font-bold">
                How your funds move together
                <FormulaTooltip
                  label="Correlation of daily returns"
                  description="Each cell compares two funds' daily returns: 1 means they rise and fall together, 0 means unrelated, below 0 means they tend to offset. Pairs near 1 in the same category are the overlaps flagged above."
                />
              </h4>
              <CorrelationHeatmap corrMatrix={h.correlation ?? []} assetNames={h.names ?? []} title="" />
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
      {data.basis === "current_mix" && !data.unavailable && (
        <p className="text-xs" style={{ color: "var(--mf-muted)" }}>Hypothetical: today&apos;s fund mix replayed over the funds&apos; own history (see the note above).</p>
      )}
      {data.unavailable ? (
        <Banner level="info">{data.reason}</Banner>
      ) : (
        <>
          {/* An equity factor model on a portfolio of liquid, arbitrage and debt funds finds
              almost nothing, and R² is exactly the measure of that. Showing the betas without
              saying so would invite reading noise as a style tilt. */}
          {data.regression!.r_squared < 0.3 && (
            <Banner level="info">
              These four equity factors explain only {pct(data.regression!.r_squared * 100, 1)} of your portfolio&apos;s daily moves. That is
              expected for a portfolio that is mostly liquid, debt or arbitrage funds, whose returns come from interest rather than from
              the stock market. Read the betas and the chart below as close to noise, not as a style tilt or as skill.
            </Banner>
          )}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
            {Object.entries(data.regression!.factor_betas).map(([k, v]) => (
              <StatCard
                key={k}
                title={`${k[0].toUpperCase()}${k.slice(1)} beta`}
                value={num(v)}
                sub={`p = ${num(data.regression!.p_values[k], 3)}${data.regression!.p_values[k] > 0.05 ? " · not significant" : ""}`}
                tooltip={<FormulaTooltip label={`${k[0].toUpperCase()}${k.slice(1)} beta`} description={FACTOR_ABOUT[k] ?? "Sensitivity of your portfolio's excess return to this factor."} />}
              />
            ))}
            <StatCard
              title="Alpha (ann.)"
              value={formatSignedPct(data.regression!.alpha_annualized_pct, 2)}
              tone={toneOf(data.regression!.alpha_annualized_pct)}
              tooltip={<FormulaTooltip label="Factor alpha" description="Annual return left over after the four factors are accounted for. On a portfolio the factors barely explain, most of the return lands here by default, so a large alpha then reflects the interest your funds earn, not stock-picking skill." />}
            />
            <StatCard
              title="Explained by factors"
              value={pct(data.regression!.r_squared * 100, 1)}
              sub={`Systematic ${pct(data.regression!.systematic_risk_pct, 0)}`}
              tooltip={<FormulaTooltip label="R² of the factor model" description="The share of your daily moves the four factors account for. High (above ~70%) means the betas describe your portfolio well; low means they describe very little of it." />}
            />
          </div>
          <h4 className="flex items-center gap-1 text-sm font-bold">
            Where your annual excess return came from
            <FormulaTooltip
              label="Return attribution"
              description="Your yearly return above the risk-free rate, split into what each factor exposure contributed (beta × that factor's return) and what is left over (alpha). Bars add up to the total."
            />
          </h4>
          <FactorWaterfallChart waterfallData={data.regression!.waterfall_data} title="" />
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
        <h3 className="flex items-center gap-1 text-base font-bold">
          Stress test: today&apos;s portfolio in past crises
          <FormulaTooltip
            label="Historical stress test"
            description="Your current funds, at today's weights, replayed through each crisis window using their own NAVs from that time. The fall is peak to trough inside the window, beside the same fall for your benchmark; recovery is how long it took to regain the prior peak."
          />
        </h3>
        <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
          <b>Hypothetical.</b> Your current fund mix held at today&apos;s weights and replayed through each crisis. Rupee figures apply that fall to today&apos;s {formatInr(data.current_value ?? 0)}.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {(data.scenarios ?? []).map((s) => (
          <div key={s.id} className="metric-card">
            <div className="metric-title">
              <span>{s.name}</span>
              <FormulaTooltip
                label={s.name}
                description={`${s.description ? `${s.description} ` : ""}Window ${formatDate(s.window_start)} to ${formatDate(s.window_end)}.`}
              />
            </div>
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
        <div className="flex items-center gap-1 text-sm font-bold">
          What-if shock
          <FormulaTooltip
            label="Parametric factor shock"
            formula="ΔP ≈ Σ βₖ × shockₖ"
            description="Move the sliders to imagine a market, size, value or momentum shock; the estimate multiplies each by your portfolio's measured sensitivity (beta) to it. It is only as good as those betas: when the factor model explains little of your portfolio, as the banner above may say, the estimate will be close to zero whatever you set."
          />
        </div>
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
  // One horizon in calendar days, driven by either control: a months slider for quick
  // moves and a days box for an exact figure. The typed text is kept separately so a
  // half-typed "4" on the way to "45" is not clamped to 30 under the cursor.
  const [horizonDays, setHorizonDays] = useState(365);
  const [draft, setDraft] = useState("365");
  const setDays = (d: number) => {
    const clamped = clampHorizonDays(d);
    setHorizonDays(clamped);
    setDraft(String(clamped));
  };
  // Each run is 1,000 simulated paths; dragging the slider must not fire one per pixel.
  const runDays = useDebouncedValue(horizonDays, 400);
  const draftValid = /^\d+$/.test(draft) && Number(draft) >= MC_MIN_DAYS && Number(draft) <= MC_MAX_DAYS;
  const { data, isFetching } = useQuery({
    queryKey: ["holdings", "mc", pid, runDays],
    queryFn: () => getMonteCarlo(pid, runDays),
    placeholderData: (prev) => prev,
  });
  const shownDays = data?.horizon_days ?? runDays;
  const fig = useMemo(() => {
    if (!data || data.empty || data.insufficient || !data.days) return null;
    // Steps are drawn at the series' own sampling rate, so they convert to calendar time
    // through the same number the backend used to size the run. A fixed 252 stretched a
    // 365-step "1Y" simulation of a liquid-fund portfolio out to 1.45 years on this axis.
    const perYear = data.obs_per_year ?? 252;
    const axis = horizonAxis(data.horizon_days ?? runDays);
    const x = data.days.map((d) => +(((d / perYear) * 365.25) / axis.perUnit).toFixed(3));
    const band = (lo: number[], hi: number[], color: string, name: string) => [
      { type: "scatter", mode: "lines", x, y: lo, line: { width: 0 }, hoverinfo: "skip", showlegend: false },
      { type: "scatter", mode: "lines", x, y: hi, fill: "tonexty", fillcolor: color, line: { width: 0 }, name, hoverinfo: "skip" },
    ];
    return {
      data: [
        ...band(data.p5!, data.p95!, "rgba(42,120,214,0.12)", "5th–95th percentile"),
        ...band(data.p25!, data.p75!, "rgba(42,120,214,0.25)", "25th–75th percentile"),
        { type: "scatter", mode: "lines", name: "Median", x, y: data.p50, line: { color: SERIES_1, width: 2 }, hovertemplate: `${axis.unit} %{x:.1f}<br>Median ₹%{y:,.0f}<extra></extra>` },
        { type: "scatter", mode: "lines", name: "Today's value", x: [0, x[x.length - 1]], y: [data.initial_value, data.initial_value], line: { color: REFERENCE, width: 1, dash: "dot" }, hoverinfo: "skip" },
      ],
      layout: { height: 320, legend: { orientation: "h", y: 1.12 }, margin: { t: 30, l: 75 }, hovermode: "x", xaxis: { ...AXIS, title: { text: axis.title } }, yaxis: { ...AXIS, tickprefix: "₹", tickformat: ",.0f" } },
    };
  }, [data, runDays]);
  if (!data || data.empty) return null;
  return (
    <section className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1 text-base font-bold">
          Range of outcomes
          <FormulaTooltip label="Monte Carlo (GBM)" description="1,000 simulated paths using your current mix's own daily drift and volatility from up to the last 3 years. It shows spread, not a forecast. Past volatility does not bound future losses." />
        </h3>
        <div className="flex flex-wrap items-center gap-3 text-xs font-semibold">
          <label className="flex items-center gap-2">
            <span style={{ color: "var(--mf-muted)" }}>Months</span>
            <input
              type="range"
              min={1}
              max={60}
              step={1}
              value={daysToMonths(horizonDays)}
              onChange={(e) => setDays(monthsToDays(Number(e.target.value)))}
              aria-label="Horizon in months, 1 to 60"
            />
          </label>
          <label className="flex items-center gap-2">
            <span style={{ color: "var(--mf-muted)" }}>or days</span>
            <input
              type="number"
              inputMode="numeric"
              min={MC_MIN_DAYS}
              max={MC_MAX_DAYS}
              step={1}
              value={draft}
              onChange={(e) => {
                setDraft(e.target.value);
                const n = Number(e.target.value);
                if (/^\d+$/.test(e.target.value) && n >= MC_MIN_DAYS && n <= MC_MAX_DAYS) setHorizonDays(n);
              }}
              onBlur={() => setDays(Number(draft))}
              onKeyDown={(e) => { if (e.key === "Enter") setDays(Number(draft)); }}
              className="w-20 rounded border px-2 py-1"
              style={{ borderColor: draftValid ? "var(--mf-border)" : "var(--mf-danger, #b91c1c)", background: "var(--mf-bg)", color: "var(--mf-fg)" }}
              aria-label={`Horizon in days, ${MC_MIN_DAYS} to ${MC_MAX_DAYS}`}
              aria-invalid={!draftValid}
            />
          </label>
          <span className="font-bold">{horizonLabel(horizonDays)}</span>
          {isFetching && <span style={{ color: "var(--mf-muted)" }}>simulating…</span>}
        </div>
      </div>
      {!draftValid && (
        <p className="-mt-1 text-xs" style={{ color: "var(--mf-danger, #b91c1c)" }}>
          Enter {MC_MIN_DAYS}–{MC_MAX_DAYS.toLocaleString("en-IN")} days (one month to five years). It will be clamped to that range when you leave the box.
        </p>
      )}
      {data.insufficient ? (
        <Banner level="info">Needs at least 60 trading days of history for the current mix.</Banner>
      ) : fig ? (
        <>
          <PlotlyChart figure={fig} />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatCard
              title="Median outcome"
              value={formatInr(data.median_terminal ?? null)}
              sub={`After ${horizonLabel(shownDays)}`}
              tooltip={<FormulaTooltip label="Median outcome" description="Half the 1,000 simulated paths end above this value and half below. It is the middle of the range, not a forecast or a target." />}
            />
            <StatCard
              title="Bad case (5th pct)"
              value={formatInr(data.p5![data.p5!.length - 1])}
              tone="neg"
              tooltip={<FormulaTooltip label="5th percentile" description="Only 1 simulated path in 20 ends below this. Past volatility does not cap future losses, so a real bad case can be worse than any simulation of it." />}
            />
            <StatCard
              title="Good case (95th pct)"
              value={formatInr(data.p95![data.p95!.length - 1])}
              tone="pos"
              tooltip={<FormulaTooltip label="95th percentile" description="Only 1 simulated path in 20 ends above this." />}
            />
            <StatCard
              title="Chance of a gain"
              value={data.prob_profit_pct !== undefined ? `${data.prob_profit_pct.toFixed(0)}%` : "-"}
              tooltip={<FormulaTooltip label="Chance of a gain" description="The share of simulated paths that end above today's value. For liquid and debt funds, which accrue interest almost every day, this is usually close to 100% over any horizon." />}
            />

          </div>
        </>
      ) : null}
    </section>
  );
}
