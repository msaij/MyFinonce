"use client";

import { useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { Banner } from "@/components/shared/Banner";
import { DataTable, type ColumnConfig } from "@/components/shared/DataTable";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { getCompare, type CompareFund } from "@/lib/api/compare";
import {
  buildCompareFigure,
  correlationBg,
  correlationCellTitle,
  feeInr,
  formatDay,
  formatNav,
  formatRatio,
  fundColor,
  leader,
  performanceLeaderPool,
  periodExplanation,
  rankText,
  ratioWithSample,
  terLeaderPool,
  valueOf,
  type ChartMode,
} from "@/lib/compare";
import { formatInr, formatSignedPct } from "@/lib/format";
import { formatTer } from "@/lib/holdings";
import { formatCrore, formatPp } from "@/lib/overview";
import { useFilterStore } from "@/lib/stores/filters";
import { RiskBadge } from "@/components/shared/RiskBadge";
import { RISK_ABOUT, riskRank } from "@/lib/riskometer";

const SECTION = "compare_simulate";
const DEFAULT_RF_PCT = 6.5;

export interface CompareTabProps {
  codes: number[];
  start: string;
  end: string;
  amount: number;
  onAmountChange: (v: number) => void;
}

const CHART_MODES: { key: ChartMode; label: (amount: number) => string; tip: string }[] = [
  {
    key: "growth",
    label: (a) => `Growth of ${formatInr(a)}`,
    tip: "Value of the amount above invested in each fund at its NAV as of the common period's start date: amount x NAV(t) / NAV(start). NAV is net of the fund's expenses; exit loads and taxes are not deducted.",
  },
  { key: "return", label: () => "% Return", tip: "Cumulative NAV return since the common period's start: NAV(t) / NAV(start) - 1. Every line starts at 0% on the same date." },
  { key: "drawdown", label: () => "Drawdown", tip: "How far each fund's NAV sits below its highest NAV so far within the common period: NAV(t) / max NAV up to t - 1. The lowest point of each line is its maximum drawdown." },
  {
    key: "rolling",
    label: () => "Rolling 1Y Return",
    tip: "Each point is the fund's trailing 1-year NAV return ending that day: NAV(t) / NAV as of t - 365 days - 1. Uses history before the window, so it exists for every day once the fund is a year old. Long periods are thinned to one point a week for the chart; the table uses every day.",
  },
  { key: "nav", label: () => "Growth % (own window)", tip: "Each fund's NAV growth over its OWN dates in the selected window: NAV(t) / its first NAV in the window - 1, so every line starts at 0% however high its NAV is (a Rs 10 fund and a Rs 1,000 fund sit on one scale). Hover shows the published NAV (Rs, 4 decimals). Unlike % Return, a fund that launched or stopped mid-window keeps its own dates here, so lines can start or end at different points." },
];

function SectionTitle({ title, tip }: { title: string; tip: string }) {
  return (
    <h2 className="mt-6 flex items-center gap-2 border-t pt-4 text-lg font-bold" style={{ borderColor: "var(--mf-border)" }}>
      {title}
      <FormulaTooltip label={title} description={tip} />
    </h2>
  );
}

function FundCell({ fund, index }: { fund: CompareFund; index: number }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: fundColor(index) }} />
      <span>{fund.display_name}</span>
      {fund.is_idcw && <span className="mf-pill mf-pill-neutral text-[10px]">IDCW</span>}
      {fund.status === "stale" && <span className="mf-pill mf-pill-neutral text-[10px]">Stopped publishing</span>}
    </span>
  );
}

function LeaderCard({ title, tip, fund, index, value }: { title: string; tip: string; fund?: CompareFund; index: number; value: string }) {
  return (
    <div className="metric-card">
      <div className="metric-title">
        <span>{title}</span>
        <FormulaTooltip label={title} description={tip} />
      </div>
      <div className="metric-value">{fund ? value : "-"}</div>
      <div className="text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
        {fund ? (
          <span className="inline-flex items-start gap-1.5">
            <span aria-hidden className="mt-1 inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: fundColor(index) }} />
            <span>{fund.display_name}</span>
          </span>
        ) : (
          "Not enough data"
        )}
      </div>
    </div>
  );
}

export function CompareTab({ codes, start, end, amount, onAmountChange }: CompareTabProps) {
  const getFilter = useFilterStore((s) => s.getFilter);
  const setFilter = useFilterStore((s) => s.setFilter);
  const [rfPct, setRfPctState] = useState<number>(() => getFilter(SECTION, "risk_free_rate_pct", DEFAULT_RF_PCT));
  const [chartMode, setChartMode] = useState<ChartMode>("growth");
  function setRfPct(v: number) {
    setRfPctState(v);
    setFilter(SECTION, "risk_free_rate_pct", v);
  }
  const rfValid = Number.isFinite(rfPct) && rfPct >= 0 && rfPct <= 20;

  const { data, error, isLoading, isFetching } = useQuery({
    queryKey: ["compare", codes, start, end, rfValid ? rfPct : DEFAULT_RF_PCT],
    queryFn: () => getCompare(codes, start || undefined, end || undefined, rfValid ? rfPct : DEFAULT_RF_PCT),
    enabled: codes.length > 0 && !!start && !!end,
    placeholderData: keepPreviousData,
  });

  const funds = useMemo(() => data?.funds ?? [], [data]);
  const indexOf = useMemo(() => new Map(funds.map((f, i) => [f.scheme_code, i])), [funds]);
  const byCode = useMemo(() => new Map(funds.map((f) => [f.scheme_code, f])), [funds]);
  const inPeriod = funds.filter((f) => f.status === "ok" && f.common);
  const figure = useMemo(() => buildCompareFigure(funds, chartMode, amount), [funds, chartMode, amount]);

  const fundCol: ColumnConfig = {
    key: "display_name",
    label: "Fund",
    tooltip: "Fund name (Plan - Option) [AMFI code]. The coloured dot matches the fund's line on every chart.",
    render: (row) => <FundCell fund={byCode.get(row.scheme_code as number)!} index={indexOf.get(row.scheme_code as number) ?? 0} />,
  };

  if (codes.length === 0) return null;
  if (isLoading) {
    return <p className="mt-4 text-sm" style={{ color: "var(--mf-muted)" }}>Computing the comparison...</p>;
  }
  if (error || !data) {
    return (
      <div className="mt-4">
        <Banner level="danger">Could not load the comparison: {error instanceof Error ? error.message : "unknown error"}</Banner>
      </div>
    );
  }

  const explanation = periodExplanation(data);
  const common = data.common;
  const periodLabel = common ? `${formatDay(common.start)} to ${formatDay(common.end)}` : "the common period";
  const amountLabel = formatInr(amount);

  const leaderPool = performanceLeaderPool(funds);
  const bestReturn = leader(leaderPool, (f) => f.common?.return_pct, "max");
  const bestSharpe = leader(leaderPool, (f) => f.risk?.sharpe, "max");
  const shallowestDd = leader(leaderPool, (f) => f.risk?.max_drawdown_pct, "max");
  const lowestTer = leader(terLeaderPool(funds), (f) => f.expense_ratio, "min");

  const returnRows = funds.map((f) => ({
    scheme_code: f.scheme_code,
    display_name: f.display_name,
    start_nav: f.common?.start_nav ?? null,
    start_date: f.common?.start_date ?? null,
    end_nav: f.common?.end_nav ?? null,
    end_date: f.common?.end_date ?? null,
    return_pct: f.common?.return_pct ?? null,
    cagr_pct: f.common?.cagr_pct ?? null,
    value: valueOf(amount, f.common?.return_pct),
    own_return_pct: f.own_window?.return_pct ?? null,
    own_dates: f.own_window ? `${formatDay(f.own_window.start_date)} to ${formatDay(f.own_window.end_date)}` : null,
  }));

  const trailingRows = funds.map((f) => ({
    scheme_code: f.scheme_code,
    display_name: f.display_name,
    latest_nav: f.latest_nav ?? null,
    latest_date: f.latest_date ?? null,
    return_30d_pct: f.return_30d_pct ?? null,
    return_1y_pct: f.return_1y_pct ?? null,
    return_3y_cagr_pct: f.return_3y_cagr_pct ?? null,
    dist_from_52w_high_pct: f.dist_from_52w_high_pct ?? null,
    high_52w: f.high_52w ?? null,
  }));

  const riskRows = funds.map((f) => ({
    scheme_code: f.scheme_code,
    display_name: f.display_name,
    vol: f.risk?.vol_ann_pct ?? null,
    obs: f.risk?.obs_per_year ?? null,
    mdd: f.risk?.max_drawdown_pct ?? null,
    mdd_dates: f.risk?.max_drawdown_trough_date ? `${formatDay(f.risk.max_drawdown_peak_date)} to ${formatDay(f.risk.max_drawdown_trough_date)}` : null,
    sharpe: f.risk?.sharpe ?? null,
    sortino: f.risk?.sortino ?? null,
    n: f.risk?.n_returns ?? null,
    short: f.risk?.short_sample ?? false,
  }));

  const rollingRows = funds.map((f) => ({
    scheme_code: f.scheme_code,
    display_name: f.display_name,
    min: f.rolling_1y?.min ?? null,
    median: f.rolling_1y?.median ?? null,
    max: f.rolling_1y?.max ?? null,
    pct_positive: f.rolling_1y?.pct_positive ?? null,
    n: f.rolling_1y?.n ?? null,
  }));

  const peerRows = funds.map((f) => ({
    scheme_code: f.scheme_code,
    display_name: f.display_name,
    peer_group: f.peer?.peer_group ?? null,
    r1: f.return_1y_pct ?? null,
    med1: f.peer?.median_1y_pct ?? null,
    rank1: f.peer?.rank_1y ?? null,
    n1: f.peer?.n_1y ?? null,
    r3: f.return_3y_cagr_pct ?? null,
    med3: f.peer?.median_3y_cagr_pct ?? null,
    rank3: f.peer?.rank_3y ?? null,
    n3: f.peer?.n_3y ?? null,
    note: f.peer?.rank_note ?? null,
  }));

  const officialRows = funds.map((f) => ({
    scheme_code: f.scheme_code,
    display_name: f.display_name,
    benchmark: f.official?.benchmark ?? null,
    f1: f.official?.fund_1y_pct ?? null,
    b1: f.official?.bench_1y_pct ?? null,
    x1: f.official?.excess_1y_pp ?? null,
    f3: f.official?.fund_3y_pct ?? null,
    b3: f.official?.bench_3y_pct ?? null,
    x3: f.official?.excess_3y_pp ?? null,
    f5: f.official?.fund_5y_pct ?? null,
    b5: f.official?.bench_5y_pct ?? null,
    x5: f.official?.excess_5y_pp ?? null,
    as_of: f.official?.as_of ?? null,
  }));

  const costRows = funds.map((f) => ({
    scheme_code: f.scheme_code,
    display_name: f.display_name,
    ter: f.expense_ratio ?? null,
    ter_as_of: f.ter_as_of_date ?? null,
    ter_status: f.ter_status ?? null,
    peer_ter: f.peer?.median_ter_pct ?? null,
    avg_ter: f.cost?.avg_ter_pct ?? null,
    fee: feeInr(amount, f.cost?.fee_pct_of_initial),
    basis: f.cost ? (f.cost.basis === "history" ? "Disclosed daily TER" : f.cost.basis === "current" ? "Current TER (no history for the period)" : `Disclosed TER for ${f.cost.history_coverage_pct.toFixed(2)}% of days, current TER for the rest`) : null,
  }));

  const detailRows = funds.map((f) => ({
    scheme_code: f.scheme_code,
    display_name: f.display_name,
    fund_house: f.fund_house ?? null,
    sebi_category: f.sebi_category ?? null,
    asset_class: f.asset_class ?? null,
    plan_type: f.plan_type ?? null,
    option_type: f.option_type ?? null,
    riskometer: f.riskometer ?? null,
    riskometer_as_of: f.riskometer_as_of ?? null,
    aum: f.aum_cr ?? null,
    isin: f.isin ?? null,
  }));

  const corr = data.correlation;
  const corrRows = corr
    ? corr.codes.map((code, i) => {
        const row: Record<string, unknown> = { scheme_code: code, display_name: byCode.get(code)?.display_name, idx: i + 1 };
        corr.codes.forEach((_, j) => (row[`c${j}`] = corr.matrix[i][j]));
        return row;
      })
    : [];
  const corrCols: ColumnConfig[] = corr
    ? [
        { key: "idx", label: "#", tooltip: "Row number; column headers #1, #2 ... refer to the funds in this order." },
        fundCol,
        ...corr.codes.map((code, j) => ({
          key: `c${j}`,
          label: `vs #${j + 1}`,
          tooltip: `Correlation with ${byCode.get(code)?.display_name ?? code}, on ${corr.basis === "weekly" ? "weekly" : "daily"} returns. Hover a cell for the number of common returns behind it.`,
          render: (row: Record<string, unknown>, v: unknown) => {
            const i = (row.idx as number) - 1;
            const title = correlationCellTitle(
              byCode.get(row.scheme_code as number)?.display_name ?? String(row.scheme_code),
              byCode.get(code)?.display_name ?? String(code),
              v as number | null,
              corr.n_obs[i]?.[j],
              corr.min_obs,
              corr.basis
            );
            return (
              <span className="inline-block rounded px-2 py-0.5" title={title} style={{ background: correlationBg(v as number | null) }}>
                {v === null || v === undefined ? "n/a" : formatRatio(v as number)}
              </span>
            );
          },
        })),
      ]
    : [];

  return (
    <div className="mt-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
          <span className="inline-flex items-center gap-1.5">
            Hypothetical investment (Rs)
            <FormulaTooltip label="Hypothetical investment" description="A lump sum invested in each fund at the start of the common period. Used for the growth chart, the value column and the fee estimate; changing it rescales them without refetching." />
          </span>
          <input
            type="number"
            min={1000}
            max={10000000}
            step={5000}
            value={amount}
            onChange={(e) => onAmountChange(Number(e.target.value))}
            className="rounded-lg border px-2 py-1.5 text-sm"
            style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
          <span className="inline-flex items-center gap-1.5">
            Risk-free rate (% a year)
            <FormulaTooltip label="Risk-free rate" description="The annual return of a riskless alternative, used by Sharpe and Sortino. Each NAV-to-NAV return is charged this rate over its own calendar days (a Friday-to-Monday return is charged three days), so funds that skip NAVs are not flattered. Default 6.50% (the app-wide default, roughly a short-term government bill yield). Set it to what you would otherwise earn risk-free." />
          </span>
          <input
            type="number"
            min={0}
            max={20}
            step={0.25}
            value={rfPct}
            onChange={(e) => setRfPct(Number(e.target.value))}
            className="rounded-lg border px-2 py-1.5 text-sm"
            style={{ borderColor: rfValid ? "var(--mf-border)" : "var(--mf-danger)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
          />
          {!rfValid && <span style={{ color: "var(--mf-danger)" }}>Enter 0 to 20; using {DEFAULT_RF_PCT.toFixed(2)}% meanwhile.</span>}
        </label>
        <div className="flex items-end text-xs" style={{ color: "var(--mf-muted)" }}>
          {isFetching ? "Updating..." : `Window: ${formatDay(data.requested.start)} to ${formatDay(data.requested.end)}`}
        </div>
      </div>

      <div className="mt-4 flex flex-col gap-2">
        {explanation.map((line, i) => (
          <Banner key={i} level={line.level}>{line.text}</Banner>
        ))}
      </div>

      {inPeriod.length > 0 && (
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <LeaderCard
            title="Highest return"
            tip={`Largest NAV return over the common period (${periodLabel}): NAV as of the end / NAV as of the start - 1. Same dates for every fund. IDCW plans are not eligible for this or the next two cards: their NAV falls by every payout, so their return, Sharpe and drawdown misstate the fund.`}
            fund={bestReturn?.fund}
            index={bestReturn ? indexOf.get(bestReturn.fund.scheme_code) ?? 0 : 0}
            value={formatSignedPct(bestReturn?.value ?? null)}
          />
          <LeaderCard
            title="Best Sharpe ratio"
            tip={`Highest Sharpe ratio over the common period, after a ${rfPct.toFixed(2)}% risk-free rate: annualised mean excess return / annualised volatility (see the risk table). A dagger marks a short sample (fewer than ${data.limits.short_sample_returns} returns), where the ratio is mostly noise. Near-riskless funds (overnight, liquid) get very large positive or negative Sharpe values from a tiny volatility, so this card says little when they are in the selection.`}
            fund={bestSharpe?.fund}
            index={bestSharpe ? indexOf.get(bestSharpe.fund.scheme_code) ?? 0 : 0}
            value={ratioWithSample(bestSharpe?.value ?? null, !!bestSharpe?.fund.risk?.short_sample)}
          />
          <LeaderCard
            title="Shallowest drawdown"
            tip="Smallest peak-to-trough NAV fall within the common period: the least-negative minimum of NAV(t) / running peak - 1, on published NAVs. 0.0000% means the NAV never fell below an earlier high (typical of liquid and overnight funds); ties go to the fund listed first."
            fund={shallowestDd?.fund}
            index={shallowestDd ? indexOf.get(shallowestDd.fund.scheme_code) ?? 0 : 0}
            value={formatSignedPct(shallowestDd?.value ?? null)}
          />
          <LeaderCard
            title="Lowest TER"
            tip="Lowest current Total Expense Ratio among the selected funds that have an official AMFI TER and are still publishing NAVs (a wound-up plan's last TER is not a cost anyone can pay today), exactly as AMFI publishes it (4 decimals). A Direct plan is normally cheaper than the Regular plan of the same fund, which pays distributor commission."
            fund={lowestTer?.fund}
            index={lowestTer ? indexOf.get(lowestTer.fund.scheme_code) ?? 0 : 0}
            value={lowestTer ? `${formatTer(lowestTer.value)}%` : "-"}
          />
        </div>
      )}

      <SectionTitle
        title="Returns over the common period"
        tip="Every fund measured between the same two dates, each valued at its NAV as of those dates (the last NAV on or before the date, so a window starting on a holiday starts from the previous NAV). The own-window columns show each fund's first-to-last NAV in the selected window, whose dates can differ between funds -- use them for reference, not for ranking."
      />
      <div className="mt-2">
        <DataTable
          keyField="scheme_code"
          rows={returnRows}
          columns={[
            fundCol,
            { key: "start_nav", label: "Start NAV", format: "number", decimals: 4, tooltip: "NAV (Rs, 4 decimals) as of the common period's start, with the date that NAV was published.", render: (r) => (r.start_nav === null ? "-" : `${formatNav(r.start_nav as number)} (${formatDay(r.start_date as string)})`) },
            { key: "end_nav", label: "End NAV", format: "number", decimals: 4, tooltip: "NAV (Rs, 4 decimals) as of the common period's end, with its publication date.", render: (r) => (r.end_nav === null ? "-" : `${formatNav(r.end_nav as number)} (${formatDay(r.end_date as string)})`) },
            { key: "return_pct", label: "Return %", format: "signed_pct", decimals: 4, tooltip: "End NAV / Start NAV - 1. NAV returns are net of the fund's expenses (TER) but before exit load and tax." },
            { key: "cagr_pct", label: "CAGR %", format: "signed_pct", decimals: 4, tooltip: "Annualised: (1 + return)^(365.25 / days) - 1, where days is the common period's calendar length (the same for every fund, even one whose start NAV is from the day before a holiday start). Shown only when the common period is at least 365 days -- annualising a shorter return states a rate the fund never earned." },
            { key: "value", label: `Value of ${amountLabel}`, format: "inr", tooltip: `${amountLabel} invested at the start NAV, valued at the end NAV: amount x (1 + return). Not a redemption payout -- exit load and capital-gains tax are not deducted.` },
            { key: "own_return_pct", label: "Own-window Return %", format: "signed_pct", decimals: 4, tooltip: "Each fund's own first-to-last NAV in the selected window. A window starting on a holiday starts from the NAV before it (if that NAV is at most 10 days earlier); a fund launched inside the window starts at its first NAV, and one that stopped publishing ends at its last. Dates can differ between funds, so do not rank on this column." },
            { key: "own_dates", label: "Own-window Dates", tooltip: "The NAV dates the own-window return runs between." },
          ]}
        />
      </div>

      <SectionTitle
        title="Visual performance comparison"
        tip="All views except Growth % (own window) use the common period, so every line starts on the same date. Lines are coloured by the fund's position in your selection and keep that colour on every chart."
      />
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {CHART_MODES.map((m) => (
          <button
            key={m.key}
            type="button"
            onClick={() => setChartMode(m.key)}
            className="rounded-full px-3 py-1.5 text-xs font-semibold"
            style={{ background: chartMode === m.key ? "var(--mf-accent-bg)" : "transparent", color: chartMode === m.key ? "var(--mf-accent)" : "var(--mf-fg)" }}
          >
            {m.label(amount)}
          </button>
        ))}
        <FormulaTooltip label={CHART_MODES.find((m) => m.key === chartMode)!.label(amount)} description={CHART_MODES.find((m) => m.key === chartMode)!.tip} />
      </div>
      <div className="mt-3">
        {figure.data.length > 0 ? (
          <PlotlyChart figure={figure} />
        ) : (
          <p className="text-sm" style={{ color: "var(--mf-muted)" }}>
            {chartMode === "rolling" ? "No fund has a full year of NAV history before these dates, so there is no rolling 1-year return to draw." : "Nothing to draw for this view."}
          </p>
        )}
      </div>

      <SectionTitle
        title="Risk over the common period"
        tip={`Computed on the NAV-to-NAV returns inside the common period. Volatility and the ratios are annualised on the number of NAVs each fund actually published a year around the period (about 245-250 for trading-day funds, 365 for funds that publish daily, fewer for funds that skip NAVs), never a fixed 252. Returns span different numbers of days (weekends, holidays, skipped NAVs); each is charged the risk-free rate (${rfPct.toFixed(2)}% a year) over its own days, and volatility is measured around the return expected for its span, so a fund is not scored on how often it publishes. Fewer than ${data.limits.short_sample_returns} returns is a short sample (marked †): the ratios are then mostly noise. Same method as the Quant page.`}
      />
      <div className="mt-2">
        <DataTable
          keyField="scheme_code"
          rows={riskRows}
          columns={[
            fundCol,
            { key: "vol", label: "Volatility (ann.) %", format: "pct", decimals: 4, tooltip: "Standard deviation of the NAV-to-NAV returns around the return expected for each one's length in days (a straight line in days fitted over the period: for a liquid fund that is its daily accrual, so a 3-day Monday return is not counted as a swing), x sqrt(NAV days a year). How much the NAV swings; not a loss measure on its own." },
            { key: "obs", label: "NAV days / yr", format: "number", decimals: 1, tooltip: "NAVs the fund published per year around the period: its NAV dates over the period, or over the year ending at the period's end when the period is shorter than a year (the whole history for a fund younger than that). The annualisation base for volatility and the ratios." },
            { key: "mdd", label: "Max Drawdown %", format: "signed_pct", decimals: 4, tooltip: "Deepest fall from a running NAV peak to a later trough within the common period: min of NAV(t) / running max - 1, on published NAVs." },
            { key: "mdd_dates", label: "Drawdown Peak to Trough", tooltip: "Dates of the peak (the last day the NAV stood at that high) and the trough that define the maximum drawdown. Blank when the NAV never fell below an earlier high." },
            { key: "sharpe", label: "Sharpe", format: "number", decimals: 4, tooltip: "Annualised mean excess return / annualised volatility: the mean of (return - Rf over that return's days) x NAV days a year, over the Volatility column. Higher is better; below 0 means the fund lagged the risk-free rate. † = short sample. Near-riskless funds (overnight, liquid) can read in the tens or hundreds either way because their volatility is tiny.", render: (r) => ratioWithSample(r.sharpe as number | null, r.short as boolean) },
            { key: "sortino", label: "Sortino", format: "number", decimals: 4, tooltip: "The same annualised mean excess return as Sharpe, over the annualised downside deviation: root mean square of the excess returns below 0, over all returns, x sqrt(NAV days a year). Like Sharpe, but only falls below the risk-free rate count as risk; the two always agree in sign. † = short sample.", render: (r) => ratioWithSample(r.sortino as number | null, r.short as boolean) },
            { key: "n", label: "Returns Used", format: "number", decimals: 0, tooltip: "Number of NAV-to-NAV returns in the common period.", render: (r) => (r.n === null ? "-" : `${r.n}${r.short ? " (short sample)" : ""}`) },
          ]}
        />
      </div>

      <SectionTitle
        title="Rolling 1-year returns"
        tip="Trailing 1-year NAV return measured on every NAV date inside the common period (each from the NAV as of 365 days earlier). Shows how consistent the fund's 1-year outcome has been, not just where it ended. A fund less than a year old on a date has no point for it."
      />
      <div className="mt-2">
        <DataTable
          keyField="scheme_code"
          rows={rollingRows}
          columns={[
            fundCol,
            { key: "min", label: "Worst 1Y %", format: "signed_pct", decimals: 4, tooltip: "Lowest trailing 1-year return among the dates in the common period." },
            { key: "median", label: "Median 1Y %", format: "signed_pct", decimals: 4, tooltip: "Middle trailing 1-year return among the dates in the common period." },
            { key: "max", label: "Best 1Y %", format: "signed_pct", decimals: 4, tooltip: "Highest trailing 1-year return among the dates in the common period." },
            { key: "pct_positive", label: "% of Days Positive", format: "pct", decimals: 2, tooltip: "Share of dates on which the trailing 1-year return was above zero." },
            { key: "n", label: "Dates", format: "number", decimals: 0, tooltip: "Number of NAV dates with a full year of history behind them." },
          ]}
        />
      </div>

      {corr && (
        <>
          <SectionTitle
            title="Correlation between the funds"
            tip={`Pearson correlation of ${corr.basis === "weekly" ? "WEEKLY" : "DAILY"} returns over the common period, each pair aligned on the dates both funds published (a daily fund's weekend accrual folds into its Monday return), each return taken net of the return expected for its length in days so two accrual funds are not "correlated" merely by sharing weekends. Weekly returns (last common NAV of each week) are used whenever the period holds at least ${corr.min_obs} of them: an overseas fund's NAV reflects the previous day's foreign close, so on daily returns two funds holding the same index can read near 0. ${corr.basis === "weekly" ? "" : "This period is too short for that, so daily returns are used: correlations involving international funds are understated. "}1 = move together, 0 = unrelated, negative = move apart. Two funds near 1 add little diversification to each other. This app does not ingest portfolio holdings, so it cannot show stock-level overlap; return correlation is the proxy. Shown as n/a with fewer than ${corr.min_obs} common returns; hover a cell for its count.`}
          />
          <div className="mt-2">
            <DataTable keyField="scheme_code" rows={corrRows} columns={corrCols} />
          </div>
        </>
      )}

      <SectionTitle
        title="Trailing returns and 52-week position"
        tip="Point-to-point NAV returns to each fund's own latest NAV, from the app's daily summary. The latest NAV date can differ between funds by a day when one AMC publishes later -- see the Latest NAV column. A fund that has stopped publishing (no NAV for 30 days) shows its last NAV but no returns or 52-week figures: they would be measured to a date that is no longer current."
      />
      <div className="mt-2">
        <DataTable
          keyField="scheme_code"
          rows={trailingRows}
          columns={[
            fundCol,
            { key: "latest_nav", label: "Latest NAV", format: "number", decimals: 4, tooltip: "Most recent published NAV (Rs, 4 decimals) and its date.", render: (r) => (r.latest_nav === null ? "-" : `${formatNav(r.latest_nav as number)} (${formatDay(r.latest_date as string)})`) },
            { key: "return_30d_pct", label: "30D %", format: "signed_pct", decimals: 4, tooltip: "Latest NAV / the NAV as of 30 days earlier (the last NAV on or before that date) - 1." },
            { key: "return_1y_pct", label: "1Y %", format: "signed_pct", decimals: 4, tooltip: "Latest NAV / the NAV as of 365 days earlier (the last NAV on or before that date) - 1. Blank for a fund less than a year old -- its since-launch return is not a 1-year return." },
            { key: "return_3y_cagr_pct", label: "3Y CAGR %", format: "signed_pct", decimals: 4, tooltip: "3-year return annualised: (latest NAV / the NAV as of 3 years earlier)^(1/3) - 1. Blank for a fund less than 3 years old." },
            { key: "dist_from_52w_high_pct", label: "From 52W High %", format: "signed_pct", decimals: 4, tooltip: "Latest NAV / highest NAV of the last 52 weeks - 1 (the 365 days to the newest NAV date in the database; since launch for a younger fund). 0% means the fund is at its 52-week high." },
            { key: "high_52w", label: "52W High NAV", format: "number", decimals: 4, tooltip: "Highest NAV of the last 52 weeks (Rs, 4 decimals): the 365 days to the newest NAV date in the database, or since launch for a younger fund." },
          ]}
        />
      </div>

      <SectionTitle
        title="Peer context"
        tip="Where each fund sits among ACTIVE schemes of its own SEBI category, asset class and plan type (Direct against Direct, Regular against Regular) -- the same peer groups as the Overview page. AMFI's older labels for the same category (e.g. 'Income/Debt Oriented Schemes - Liquid Fund') are grouped with the current ones, and a category that mixes asset classes ('Index Funds', 'Other ETFs', 'FoF Domestic') is split by asset class, so a Nifty 50 index fund is not ranked against target-maturity debt index funds. IDCW plans are left out of the peer group because payouts depress their NAV returns, and funds younger than the horizon are left out of that horizon (a since-launch return is not a 1- or 3-year return). Rank 1 = best return in the group; tied returns share a rank. From the daily summary, to each fund's latest NAV."
      />
      <div className="mt-2">
        <DataTable
          keyField="scheme_code"
          rows={peerRows}
          columns={[
            fundCol,
            { key: "peer_group", label: "Peer Group", tooltip: "SEBI category - plan type the fund is ranked in; the asset class is added in brackets where the category holds more than one." },
            { key: "r1", label: "Fund 1Y %", format: "signed_pct", decimals: 4, tooltip: "The fund's trailing 1-year NAV return." },
            { key: "med1", label: "Peer Median 1Y %", format: "signed_pct", decimals: 4, tooltip: "Median trailing 1-year return of the peer group." },
            { key: "rank1", label: "1Y Rank", tooltip: "Rank within the peer group on 1-year return (1 = best), out of the peers that have a 1-year return.", render: (r) => (r.note ? "n/a (IDCW)" : rankText(r.rank1 as number | null, r.n1 as number | null)) },
            { key: "r3", label: "Fund 3Y CAGR %", format: "signed_pct", decimals: 4, tooltip: "The fund's 3-year return, annualised." },
            { key: "med3", label: "Peer Median 3Y CAGR %", format: "signed_pct", decimals: 4, tooltip: "Median annualised 3-year return of the peer group." },
            { key: "rank3", label: "3Y Rank", tooltip: "Rank within the peer group on 3-year return (1 = best), out of the peers that have 3 years of history.", render: (r) => (r.note ? "n/a (IDCW)" : rankText(r.rank3 as number | null, r.n3 as number | null)) },
          ]}
        />
      </div>

      <SectionTitle
        title="AMFI official returns vs SEBI benchmark"
        tip="Returns AMFI itself publishes for the fund's plan and for the benchmark SEBI requires it to be measured against (a Total Return Index), from AMFI's fund-performance snapshot. 3Y and 5Y are annualised. Excess = fund minus benchmark, in percentage points. AMFI computes these on the Growth NAV, so IDCW plans show the benchmark only."
      />
      <div className="mt-2">
        <DataTable
          keyField="scheme_code"
          rows={officialRows}
          columns={[
            fundCol,
            { key: "benchmark", label: "SEBI Benchmark", tooltip: "The benchmark index the scheme declares under SEBI rules." },
            { key: "f1", label: "Fund 1Y %", format: "signed_pct", decimals: 4, tooltip: "AMFI's published 1-year return for this plan." },
            { key: "b1", label: "Benchmark 1Y %", format: "signed_pct", decimals: 4, tooltip: "AMFI's published 1-year benchmark return." },
            { key: "x1", label: "Excess 1Y", tooltip: "Fund 1Y minus benchmark 1Y, in percentage points.", render: (r) => formatPp(r.x1 as number | null, 4) },
            { key: "f3", label: "Fund 3Y %", format: "signed_pct", decimals: 4, tooltip: "AMFI's published 3-year annualised return for this plan." },
            { key: "b3", label: "Benchmark 3Y %", format: "signed_pct", decimals: 4, tooltip: "AMFI's published 3-year annualised benchmark return." },
            { key: "x3", label: "Excess 3Y", tooltip: "Fund 3Y minus benchmark 3Y, in percentage points.", render: (r) => formatPp(r.x3 as number | null, 4) },
            { key: "f5", label: "Fund 5Y %", format: "signed_pct", decimals: 4, tooltip: "AMFI's published 5-year annualised return for this plan." },
            { key: "b5", label: "Benchmark 5Y %", format: "signed_pct", decimals: 4, tooltip: "AMFI's published 5-year annualised benchmark return." },
            { key: "x5", label: "Excess 5Y", tooltip: "Fund 5Y minus benchmark 5Y, in percentage points.", render: (r) => formatPp(r.x5 as number | null, 4) },
            { key: "as_of", label: "AMFI As Of", format: "date", tooltip: "Date of AMFI's snapshot these figures come from." },
          ]}
        />
      </div>

      <SectionTitle
        title="Costs (TER)"
        tip="Total Expense Ratio exactly as AMFI publishes it (4 decimals). NAV returns above are already net of it. The fee estimate is what the fund charged on the hypothetical investment over the common period: the sum over every calendar day of value held that day x that day's TER / 365, using the TER AMFI disclosed for that day where the app has it (daily disclosures from April 2026) and the current official TER for earlier days -- for older periods that is only an approximation, since TERs change over time."
      />
      <div className="mt-2">
        <DataTable
          keyField="scheme_code"
          rows={costRows}
          columns={[
            fundCol,
            { key: "ter", label: "TER %", format: "pct", decimals: 4, tooltip: "Current Total Expense Ratio, a year's fee as a % of assets, as AMFI publishes it." },
            { key: "ter_as_of", label: "TER As Of", format: "date", tooltip: "Date of the AMFI TER disclosure the current TER comes from." },
            { key: "ter_status", label: "TER Confidence", tooltip: "official = dated AMFI TER disclosure; legacy_unverified = an older unverified figure (not used for the fee estimate); unknown = no TER on record." },
            { key: "peer_ter", label: "Peer Median TER %", format: "pct", decimals: 4, tooltip: "Median current official TER of the peer group (same SEBI category, asset class and plan type, active schemes, IDCW excluded). Computed by the app, shown to 4 decimals like AMFI's figures." },
            { key: "avg_ter", label: "Avg TER Applied %", format: "pct", decimals: 4, tooltip: "Average of the TER applied on each calendar day of the common period for the fee estimate (the disclosed daily TER where available, else the current TER). Computed by the app, shown to 4 decimals." },
            { key: "fee", label: `Est. Fees on ${amountLabel}`, format: "inr", tooltip: `Estimated rupees the fund charged on ${amountLabel} over ${periodLabel}. Already reflected in the NAV return -- this shows what the cost was, it is not deducted again.` },
            { key: "basis", label: "TER Basis", tooltip: "Which TER figures the estimate used: AMFI's disclosed TER for every day, the current TER for every day (no disclosure in the app covers the period), or a mix with the share of days that had a disclosure." },
          ]}
        />
      </div>

      <SectionTitle
        title="Fund details"
        tip="Identity and size. Riskometer is SEBI's six-level risk label (Low to Very High) set by the AMC. AUM is AMFI's figure for the whole fund, all plans and options together, in Rs crore."
      />
      <div className="mt-2">
        <DataTable
          keyField="scheme_code"
          rows={detailRows}
          columns={[
            fundCol,
            { key: "fund_house", label: "Fund House", tooltip: "The asset management company." },
            { key: "sebi_category", label: "SEBI Category", tooltip: "The scheme's SEBI category." },
            { key: "asset_class", label: "Asset Class", tooltip: "The app's asset-class grouping of the category (liquid, overnight and arbitrage funds count as Cash & Liquid)." },
            { key: "plan_type", label: "Plan", tooltip: "Direct (no distributor commission) or Regular." },
            { key: "option_type", label: "Option", tooltip: "Growth reinvests everything; IDCW pays out, and its NAV drops by each payout." },
            {
              key: "riskometer",
              label: "Riskometer",
              tooltip: RISK_ABOUT,
              sortValue: (r) => riskRank(r.riskometer),
              render: (r) => <RiskBadge level={r.riskometer as string | null} asOf={r.riskometer_as_of as string | null} emptyText="-" />,
            },
            { key: "aum", label: "Fund AUM", tooltip: "Assets under management of the whole fund (all plans), from AMFI's snapshot.", render: (r) => formatCrore(r.aum as number | null) },
            { key: "isin", label: "ISIN", tooltip: "The plan's ISIN (growth / payout)." },
          ]}
        />
      </div>
    </div>
  );
}
