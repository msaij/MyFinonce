"use client";

import { useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";

import { Banner } from "@/components/shared/Banner";
import { DataTable, type ColumnConfig } from "@/components/shared/DataTable";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { SearchCombobox } from "@/components/shared/SearchCombobox";
import { StatCard } from "@/components/shared/StatCard";
import {
  runBacktest,
  type BacktestMode,
  type BacktestResult,
  type BenchmarkKind,
  type RebalanceFreq,
} from "@/lib/api/backtest";
import {
  SIP_DAY_CHOICES,
  effectiveWeights,
  equalWeights,
  formatShare,
  fundColor,
  ordinal,
  parseWeightsParam,
  rollingReturns,
  serializeWeights,
  sipDayLabel,
  summarizeWeights,
  xirrNoteText,
} from "@/lib/backtest";
import { formatDate, formatInr, formatSignedPct, toneOf } from "@/lib/format";
import { formatSignedInr, formatTer } from "@/lib/holdings";
import { AXIS, REFERENCE, SERIES_1, SERIES_2 } from "@/lib/holdingsChart";
import { useDebouncedValue, useUrlSync } from "@/lib/hooks";
import { portfolio_sim_REBALANCE } from "@/lib/rebalance";
import { useFilterStore } from "@/lib/stores/filters";

const CFG = "portfolio_config";
const WEIGHTS = "portfolio_weights";
const DEFAULT_LUMP_SUM = 100000;
const DEFAULT_SIP = 5000;

type ChartTab = "value" | "twr" | "drawdown" | "alloc" | "rolling" | "years";

const CHART_TABS: { key: ChartTab; label: string }[] = [
  { key: "value", label: "Value vs invested" },
  { key: "twr", label: "Growth of 100 (TWR)" },
  { key: "drawdown", label: "Drawdown" },
  { key: "alloc", label: "Allocation drift" },
  { key: "rolling", label: "Rolling returns" },
  { key: "years", label: "Calendar years" },
];

const inputStyle = { borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" } as const;
const labelClass = "flex flex-col gap-1 text-xs font-medium";
const muted = { color: "var(--mf-muted)" } as const;

function Tip({ label, formula, text }: { label: string; formula?: string; text: string }) {
  return <FormulaTooltip label={label} formula={formula} description={text} />;
}

function Label({ text, tip }: { text: string; tip: string }) {
  return (
    <span className="inline-flex items-center gap-1" style={muted}>
      {text}
      <Tip label={text} text={tip} />
    </span>
  );
}

const pctCell = (_r: Record<string, unknown>, v: unknown) => {
  const n = v as number | null | undefined;
  if (n === null || n === undefined) return "-";
  return <span className={n > 0 ? "mf-pos" : n < 0 ? "mf-neg" : ""}>{formatSignedPct(n, 2)}</span>;
};

export interface BacktestPanelProps {
  selectedCodes: number[];
  /** code -> "Name (Plan - Option) [code]" from the page's scheme profiles. */
  schemeNames: Map<number, string>;
  start: string;
  end: string;
}

export function BacktestPanel({ selectedCodes, schemeNames, start, end }: BacktestPanelProps) {
  const searchParams = useSearchParams();
  const getFilter = useFilterStore((s) => s.getFilter);
  const setFilter = useFilterStore((s) => s.setFilter);

  // --- Inputs (URL first, then this browser's last choice, then the default) ------------
  const [weights, setWeights] = useState<Record<number, number>>(() => {
    const fromUrl = parseWeightsParam(searchParams.get("weights"));
    const w: Record<number, number> = {};
    for (const c of selectedCodes) {
      const stored = fromUrl[c] ?? getFilter<number | undefined>(WEIGHTS, String(c), undefined);
      if (stored !== undefined) w[c] = stored;
    }
    return w;
  });
  const [mode, setModeState] = useState<BacktestMode>(() => {
    const p = searchParams.get("mode");
    return p === "Lump Sum" || p === "SIP (Monthly)" ? p : getFilter<BacktestMode>(CFG, "mode", "Lump Sum");
  });
  const numParam = (k: string) => {
    const v = searchParams.get(k);
    return v !== null && v !== "" && Number.isFinite(Number(v)) ? Number(v) : undefined;
  };
  const [lumpSum, setLumpSumState] = useState<number>(() => numParam("lump_sum") ?? getFilter(CFG, "lump_sum_amount", DEFAULT_LUMP_SUM));
  const [sipAmount, setSipAmountState] = useState<number>(() => numParam("sip_amount") ?? getFilter(CFG, "sip_amount", DEFAULT_SIP));
  const [rebalance, setRebalanceState] = useState<RebalanceFreq>(() => {
    const p = searchParams.get("rebalance");
    const v = p ?? getFilter<string>(CFG, "rebalance", "None");
    return (portfolio_sim_REBALANCE as readonly string[]).includes(v) ? (v as RebalanceFreq) : "None";
  });
  const [sipDay, setSipDayState] = useState<"start" | number>(() => {
    const p = numParam("sip_day");
    if (p !== undefined && p >= 1 && p <= 31) return p;
    return getFilter<"start" | number>(CFG, "sip_day", "start");
  });
  const [benchmark, setBenchmarkState] = useState<BenchmarkKind>(() => {
    const p = searchParams.get("bench");
    const v = p ?? getFilter<string>(CFG, "benchmark", "category");
    return v === "none" || v === "scheme" || v === "category" ? v : "category";
  });
  const [benchCode, setBenchCodeState] = useState<number | null>(() => numParam("bench_code") ?? getFilter<number | null>(CFG, "benchmark_code", null));
  const [stampDuty, setStampDutyState] = useState<boolean>(() =>
    searchParams.get("stamp") === "off" ? false : getFilter<boolean>(CFG, "apply_stamp_duty", true)
  );
  const [exitLoad, setExitLoadState] = useState<number>(() => numParam("exit_load") ?? getFilter<number>(CFG, "exit_load_pct", 0));
  const [chartTab, setChartTab] = useState<ChartTab>("value");
  const [rollingYears, setRollingYears] = useState<1 | 3>(1);

  const persist = <T,>(setter: (v: T) => void, key: string) => (v: T) => {
    setter(v);
    setFilter(CFG, key, v);
  };
  const setMode = persist(setModeState, "mode");
  const setLumpSum = persist(setLumpSumState, "lump_sum_amount");
  const setSipAmount = persist(setSipAmountState, "sip_amount");
  const setRebalance = persist(setRebalanceState, "rebalance");
  const setSipDay = persist(setSipDayState, "sip_day");
  const setBenchmark = persist(setBenchmarkState, "benchmark");
  const setBenchCode = persist(setBenchCodeState, "benchmark_code");
  const setStampDuty = persist(setStampDutyState, "apply_stamp_duty");
  const setExitLoad = persist(setExitLoadState, "exit_load_pct");

  const eff = effectiveWeights(selectedCodes, weights);
  const summary = summarizeWeights(selectedCodes, eff);

  function setWeight(code: number, v: number) {
    setWeights({ ...eff, [code]: v });
    setFilter(WEIGHTS, String(code), v);
  }
  function resetEqual() {
    const eq = equalWeights(selectedCodes);
    setWeights(eq);
    for (const c of selectedCodes) setFilter(WEIGHTS, String(c), eq[c]);
  }

  useUrlSync({
    mode: mode !== "Lump Sum" ? mode : undefined,
    rebalance: rebalance !== "None" ? rebalance : undefined,
    lump_sum: mode === "Lump Sum" && lumpSum !== DEFAULT_LUMP_SUM ? lumpSum : undefined,
    sip_amount: mode === "SIP (Monthly)" && sipAmount !== DEFAULT_SIP ? sipAmount : undefined,
    weights: serializeWeights(selectedCodes, eff),
    sip_day: mode === "SIP (Monthly)" && sipDay !== "start" ? sipDay : undefined,
    bench: benchmark !== "category" ? benchmark : undefined,
    bench_code: benchmark === "scheme" && benchCode ? benchCode : undefined,
    stamp: stampDuty ? undefined : "off",
    exit_load: exitLoad > 0 ? exitLoad : undefined,
  });

  // --- Request (debounced so typing an amount does not fire one run per keystroke) -------
  const request = useMemo(
    () => ({
      scheme_codes: selectedCodes,
      weights: Object.fromEntries(selectedCodes.map((c) => [c, eff[c] ?? 0])),
      mode,
      lump_sum_amount: mode === "Lump Sum" ? lumpSum : 0,
      sip_amount: mode === "SIP (Monthly)" ? sipAmount : 0,
      rebalance_freq: rebalance,
      start_date: start,
      end_date: end,
      sip_day: sipDay === "start" ? null : sipDay,
      apply_stamp_duty: stampDuty,
      exit_load_pct: exitLoad,
      benchmark: benchmark === "scheme" && !benchCode ? ("none" as const) : benchmark,
      benchmark_code: benchmark === "scheme" ? benchCode : null,
    }),
    [JSON.stringify(selectedCodes), JSON.stringify(eff), mode, lumpSum, sipAmount, rebalance, start, end, sipDay, stampDuty, exitLoad, benchmark, benchCode]
  );
  const debounced = useDebouncedValue(request, 400);
  const amountOk = (mode === "Lump Sum" ? lumpSum : sipAmount) > 0;
  const canRun = selectedCodes.length > 0 && summary.sum > 0 && !summary.hasNegative && amountOk && !!start && !!end;

  const { data, isFetching, isError, error } = useQuery({
    queryKey: ["backtest", debounced],
    queryFn: () => runBacktest(debounced),
    enabled: canRun,
    placeholderData: keepPreviousData,
  });
  const ok: BacktestResult | undefined = data && !data.error ? data : undefined;
  const bench = ok?.benchmark && ok.benchmark.kind !== "none" && !ok.benchmark.error ? ok.benchmark : undefined;
  const nameOf = (c: number) => ok?.funds?.find((f) => f.scheme_code === c)?.display_name ?? schemeNames.get(c) ?? String(c);
  const simulatedCodes = ok?.funds?.map((f) => f.scheme_code) ?? [];

  // --- Figures ------------------------------------------------------------------------
  const figs = useMemo(() => {
    const rows = ok?.df_result ?? [];
    if (!ok || rows.length === 0) return null;
    const x = rows.map((r) => r.nav_date);
    const hasBench = !!bench && rows.some((r) => r.benchmark_value !== null && r.benchmark_value !== undefined);
    const benchName = bench?.kind === "category" ? "Category peers" : bench?.name ?? "Benchmark";
    const legend = { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "left", x: 0 };

    const value = {
      data: [
        { type: "scatter", mode: "lines", name: "Portfolio value", x, y: rows.map((r) => r.portfolio_value), line: { color: SERIES_1, width: 2 },
          hovertemplate: "Portfolio ₹%{y:,.2f}<extra></extra>" },
        ...(hasBench
          ? [{ type: "scatter", mode: "lines", name: `${benchName} (same cash flows)`, x, y: rows.map((r) => r.benchmark_value ?? null),
              line: { color: SERIES_2, width: 2 }, hovertemplate: "Benchmark ₹%{y:,.2f}<extra></extra>" }]
          : []),
        { type: "scatter", mode: "lines", name: "Money invested", x, y: rows.map((r) => r.total_invested), line: { color: REFERENCE, width: 2, dash: "dot", shape: "hv" },
          hovertemplate: "Invested ₹%{y:,.2f}<extra></extra>" },
      ],
      layout: { height: 420, legend, yaxis: { ...AXIS, tickprefix: "₹", tickformat: ",.0f" }, xaxis: { ...AXIS }, margin: { l: 80 } },
    };
    const twr = {
      data: [
        { type: "scatter", mode: "lines", name: "Portfolio (TWR)", x, y: rows.map((r) => r.twr_index), line: { color: SERIES_1, width: 2 },
          hovertemplate: "Portfolio %{y:.4f}<extra></extra>" },
        ...(hasBench
          ? [{ type: "scatter", mode: "lines", name: benchName, x, y: rows.map((r) => r.benchmark_index ?? null), line: { color: SERIES_2, width: 2 },
              hovertemplate: "Benchmark %{y:.4f}<extra></extra>" }]
          : []),
      ],
      layout: {
        height: 420, legend, yaxis: { ...AXIS, title: { text: "Growth of 100" } }, xaxis: { ...AXIS },
        shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 100, y1: 100, line: { color: REFERENCE, width: 1, dash: "dot" } }],
      },
    };
    const drawdown = {
      data: [
        { type: "scatter", mode: "lines", name: "Portfolio drawdown", x, y: rows.map((r) => r.drawdown_pct), line: { color: SERIES_1, width: 2 },
          fill: "tozeroy", fillcolor: "rgba(42,120,214,0.12)", hovertemplate: "Portfolio %{y:.2f}%<extra></extra>" },
        ...(hasBench
          ? [{ type: "scatter", mode: "lines", name: `${benchName} drawdown`, x, y: rows.map((r) => r.benchmark_drawdown_pct ?? null),
              line: { color: SERIES_2, width: 2 }, hovertemplate: "Benchmark %{y:.2f}%<extra></extra>" }]
          : []),
      ],
      layout: { height: 380, legend, yaxis: { ...AXIS, ticksuffix: "%", rangemode: "tozero" }, xaxis: { ...AXIS } },
    };
    const alloc = {
      data: simulatedCodes.map((c) => {
        const i = selectedCodes.indexOf(c);
        return {
          type: "scatter", mode: "lines", stackgroup: "one", name: nameOf(c), x,
          y: rows.map((r) => {
            const total = simulatedCodes.reduce((s, cc) => s + ((r[`holding_${cc}`] as number) ?? 0), 0);
            return total > 0 ? (((r[`holding_${c}`] as number) ?? 0) / total) * 100 : null;
          }),
          line: { color: fundColor(i < 0 ? 0 : i), width: 1 },
          hovertemplate: "%{y:.2f}%<extra>%{fullData.name}</extra>",
        };
      }),
      layout: { height: 420, legend: { orientation: "h", yanchor: "top", y: -0.12, xanchor: "left", x: 0 }, yaxis: { ...AXIS, ticksuffix: "%", range: [0, 100] }, xaxis: { ...AXIS } },
    };
    const days = rollingYears * 365;
    const roll = rollingReturns(x, rows.map((r) => r.twr_index), days);
    const rollBench = hasBench
      ? rollingReturns(
          rows.filter((r) => r.benchmark_index != null).map((r) => r.nav_date),
          rows.filter((r) => r.benchmark_index != null).map((r) => r.benchmark_index as number),
          days
        )
      : { x: [], y: [] };
    const rolling = {
      data: [
        { type: "scatter", mode: "lines", name: "Portfolio", x: roll.x, y: roll.y, line: { color: SERIES_1, width: 2 },
          hovertemplate: "Portfolio %{y:.2f}%<extra></extra>" },
        ...(rollBench.x.length
          ? [{ type: "scatter", mode: "lines", name: benchName, x: rollBench.x, y: rollBench.y, line: { color: SERIES_2, width: 2 },
              hovertemplate: "Benchmark %{y:.2f}%<extra></extra>" }]
          : []),
      ],
      layout: {
        height: 380, legend, yaxis: { ...AXIS, ticksuffix: "%", zeroline: true }, xaxis: { ...AXIS },
      },
      empty: roll.x.length === 0,
    };
    const yrs = ok.calendar_years ?? [];
    const yearLabel = (y: { year: number; partial: boolean }) => `${y.year}${y.partial ? "*" : ""}`;
    const years = {
      data: [
        { type: "bar", name: "Portfolio (TWR)", x: yrs.map(yearLabel), y: yrs.map((y) => y.portfolio_pct), marker: { color: SERIES_1, cornerradius: 4 },
          text: yrs.map((y) => formatSignedPct(y.portfolio_pct, 1)), textposition: "outside", cliponaxis: false,
          hovertemplate: "Portfolio %{y:.2f}%<extra>%{x}</extra>" },
        ...(hasBench
          ? [{ type: "bar", name: benchName, x: yrs.map(yearLabel), y: yrs.map((y) => y.benchmark_pct), marker: { color: SERIES_2, cornerradius: 4 },
              hovertemplate: "Benchmark %{y:.2f}%<extra>%{x}</extra>" }]
          : []),
      ],
      layout: { height: 380, barmode: "group", bargap: 0.3, bargroupgap: 0.08, legend, yaxis: { ...AXIS, ticksuffix: "%", zeroline: true }, xaxis: { type: "category" } },
    };
    return { value, twr, drawdown, alloc, rolling, years };
  }, [ok, bench, rollingYears, JSON.stringify(selectedCodes)]);

  // --- Tables ---------------------------------------------------------------------------
  const fundColumns: ColumnConfig[] = [
    { key: "display_name", label: "Fund", tooltip: "The fund, as 'Name (Plan - Option) [AMFI code]'." },
    { key: "category", label: "Category", tooltip: "AMFI/SEBI category. The category-peer benchmark uses this." },
    { key: "expense_ratio", label: "TER %", sortValue: (r) => r.expense_ratio,
      render: (_r, v) => (v === null || v === undefined ? "-" : `${formatTer(v as number)}%`),
      tooltip: "Current Total Expense Ratio exactly as AMFI publishes it (4 decimals). Already deducted inside every daily NAV, so the backtest does not subtract it again. Today's TER, not the TER in force during the window." },
    { key: "target_weight_pct", label: "Target", render: (_r, v) => formatShare(v as number),
      tooltip: "Your weight for this fund, rescaled so all weights add up to 100%." },
    { key: "final_weight_pct", label: "End weight", render: (_r, v) => formatShare(v as number),
      tooltip: "The fund's share of the portfolio's value on the last day. Differs from the target by the drift since the last rebalance." },
    { key: "contributed", label: "Invested", format: "inr",
      tooltip: "Your contributions allocated to this fund (gross, including stamp duty)." },
    { key: "rebalance_net", label: "Rebalancing (net)", sortValue: (r) => r.rebalance_net, render: (_r, v) => formatSignedInr(v as number),
      tooltip: "Money switched into this fund by rebalancing minus money switched out of it (after exit loads). Positive: rebalancing topped it up." },
    { key: "final_value", label: "End value", format: "inr", tooltip: "Units held on the last day x that day's NAV." },
    { key: "gain", label: "Gain", sortValue: (r) => r.gain, render: (_r, v) => <span className={(v as number) >= 0 ? "mf-pos" : "mf-neg"}>{formatSignedInr(v as number)}</span>,
      tooltip: "End value + money switched out - money put in (contributions and switch-ins). The funds' gains add up exactly to the portfolio's gain." },
    { key: "gain_share_pct", label: "Share of gain", render: (_r, v) => formatShare(v as number, 1),
      tooltip: "This fund's gain as a share of the portfolio's total gain. Can exceed 100% or go negative when another fund lost money." },
    { key: "xirr_pct", label: "Fund XIRR", render: (r, v) => (v === null || v === undefined ? xirrNoteText(r.xirr_note as string) : pctCell(r, v)),
      tooltip: "Money-weighted annual return of this fund's own cash flows: its share of every contribution, its rebalancing switches, and its end value. Withheld under 30 days." },
    { key: "nav_return_pct", label: "NAV return", render: pctCell,
      tooltip: "The fund's NAV change over the simulated window (not annualised): what one unit bought on day one did, ignoring your cash-flow timing. On an IDCW option this understates the return by the payouts." },
    { key: "nav_start", label: "NAV (start)", format: "number", decimals: 4, tooltip: "NAV on the first simulated day, 4 decimals as AMFI publishes it." },
    { key: "nav_end", label: "NAV (end)", format: "number", decimals: 4, tooltip: "NAV on the last simulated day." },
  ];
  const fundRows = (ok?.funds ?? []).map((f) => ({ ...f, rebalance_net: f.rebalance_bought - f.rebalance_sold }));
  const fundFooter = ok
    ? {
        display_name: "Portfolio",
        target_weight_pct: "100.00%",
        contributed: formatInr(ok.total_invested ?? 0),
        final_value: formatInr(ok.final_value ?? 0),
        gain: formatSignedInr(ok.absolute_gain ?? 0),
        xirr_pct: ok.money_weighted_xirr_pct != null ? formatSignedPct(ok.money_weighted_xirr_pct, 2) : xirrNoteText(ok.xirr_note),
      }
    : undefined;

  const yearColumns: ColumnConfig[] = [
    { key: "label", label: "Year", sortable: false, tooltip: "Calendar year. * = partial: the window starts after the first week of January or ends before 24 December." },
    { key: "portfolio_pct", label: "Portfolio (TWR)", render: pctCell, tooltip: "The time-weighted index's change over the year (previous year's last value to this year's last value). Contributions do not count as returns." },
    ...(bench ? [{ key: "benchmark_pct", label: "Benchmark", render: pctCell, tooltip: "The benchmark run's time-weighted change over the same dates." } as ColumnConfig] : []),
    ...simulatedCodes.map(
      (c) => ({ key: `f_${c}`, label: nameOf(c), render: pctCell, tooltip: `NAV change of ${nameOf(c)} over the same dates (not your money-weighted return in it).` }) as ColumnConfig
    ),
  ];
  const yearRows = (ok?.calendar_years ?? []).map((y) => ({
    label: `${y.year}${y.partial ? "*" : ""}`,
    portfolio_pct: y.portfolio_pct,
    benchmark_pct: y.benchmark_pct,
    ...Object.fromEntries(simulatedCodes.map((c) => [`f_${c}`, y.funds[String(c)] ?? null])),
  }));

  const tradeColumns: ColumnConfig[] = [
    { key: "date", label: "Date", format: "date", tooltip: "Allotment date: the scheduled date, or the next date on which every fund published a NAV if it fell on a weekend or holiday." },
    { key: "type", label: "Type", tooltip: "SIP instalment / lump sum (money in), or a rebalancing switch between your funds." },
    { key: "display_name", label: "Fund" },
    { key: "amount", label: "Amount", sortValue: (r) => r.amount, render: (_r, v) => formatSignedInr(v as number),
      tooltip: "Rupees into (+) or out of (-) the fund. A buy's amount includes the stamp duty deducted from it." },
    { key: "nav", label: "NAV", format: "number", decimals: 4, tooltip: "The NAV the order was allotted at, on the fund's current unit basis: if a fund later split its units (an ETF going 10:1, say), earlier NAVs are shown divided by the split and units multiplied by it, so value and returns are unchanged." },
    { key: "units", label: "Units", format: "number", decimals: 4, tooltip: "Units allotted (+) or redeemed (-). Not rounded to 3 decimals as an AMC would." },
    { key: "stamp_duty", label: "Stamp duty", format: "inr", tooltip: "0.005% of a purchase, from 1 July 2020 (if switched on)." },
    { key: "exit_load", label: "Exit load", format: "inr", tooltip: "Your exit-load assumption on units sold within a year of purchase (first-in-first-out)." },
  ];

  // --- Render ------------------------------------------------------------------------------
  const tm = ok?.twr_metrics;
  const withheld = !!tm?.annualise_withheld;
  const a = ok?.assumptions;
  const w = ok?.window;

  return (
    <div className="mt-4 flex flex-col gap-5">
      <Banner level="info">
        A historical backtest on official AMFI NAVs over the time horizon selected above -- not a projection. NAVs are already net
        of each fund&apos;s expense ratio. Stamp duty and an optional exit load are applied; capital-gains tax is not. Full list under
        &quot;Assumptions&quot; at the bottom.
      </Banner>

      {/* --- Setup ----------------------------------------------------------------------- */}
      <section className="filter-box">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="flex items-center gap-1 text-sm font-bold">
            Target allocation
            <Tip label="Target allocation" text="How each contribution is split, and what rebalancing restores. Any positive numbers work: they are rescaled to add up to 100%. A fund at 0% is left out of the simulation (and cannot shorten its window)." />
          </h3>
          <button type="button" onClick={resetEqual} className="rounded-lg border px-3 py-1 text-xs font-semibold" style={inputStyle}>
            Equal weights
          </button>
        </div>
        <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
          {selectedCodes.map((code, i) => (
            <label key={code} className={labelClass} style={muted}>
              <span className="flex items-start gap-1.5">
                <span aria-hidden className="mt-1 inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: fundColor(i) }} />
                <span style={{ color: "var(--mf-fg)" }}>{schemeNames.get(code) ?? String(code)}</span>
              </span>
              <span className="flex items-center gap-2">
                <input type="number" min={0} step={1} value={eff[code] ?? 0}
                  onChange={(e) => setWeight(code, Math.max(0, Number(e.target.value)))}
                  className="w-28 rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} />
                <span>= {formatShare(summary.normalized[code], 2)}</span>
              </span>
            </label>
          ))}
        </div>
        {summary.sum <= 0 ? (
          <p className="mt-2 text-xs" style={{ color: "var(--mf-danger)" }}>At least one fund needs a positive weight.</p>
        ) : summary.rescaled ? (
          <p className="mt-2 text-xs" style={muted}>
            Weights add up to <b style={{ color: "var(--mf-fg)" }}>{summary.sum.toFixed(2)}</b> -- rescaled proportionally to 100% (shown after each box).
          </p>
        ) : null}

        <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3 xl:grid-cols-4">
          <label className={labelClass}>
            <Label text="Investment mode" tip="Lump sum: the whole amount goes in on the first day of the window. SIP: the amount goes in every month on the SIP date." />
            <select value={mode} onChange={(e) => setMode(e.target.value as BacktestMode)} className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle}>
              <option className="bg-white text-slate-900">Lump Sum</option>
              <option className="bg-white text-slate-900">SIP (Monthly)</option>
            </select>
          </label>
          {mode === "Lump Sum" ? (
            <label className={labelClass}>
              <Label text="Lump sum amount (₹)" tip="Invested once, on the first date in the window on which every selected fund published a NAV." />
              <input type="number" min={1} step={5000} value={lumpSum} onChange={(e) => setLumpSum(Number(e.target.value))}
                className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} />
            </label>
          ) : (
            <>
              <label className={labelClass}>
                <Label text="Monthly SIP amount (₹)" tip="Invested every month, split across the funds by the target weights." />
                <input type="number" min={1} step={500} value={sipAmount} onChange={(e) => setSipAmount(Number(e.target.value))}
                  className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} />
              </label>
              <label className={labelClass}>
                <Label text="SIP date" tip="The day of the month each instalment is due. If it is a weekend or market holiday, the instalment is allotted at the next date on which every selected fund published a NAV -- as an AMC does. Days past a month's end (e.g. the 31st in February) fall on its last day. The first instalment is the first SIP date on or after the window's start." />
                <select value={String(sipDay)} onChange={(e) => setSipDay(e.target.value === "start" ? "start" : Number(e.target.value))}
                  className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle}>
                  {SIP_DAY_CHOICES.map((d) => (
                    <option key={String(d)} value={String(d)} className="bg-white text-slate-900">{sipDayLabel(d)}</option>
                  ))}
                </select>
              </label>
            </>
          )}
          <label className={labelClass}>
            <Label text="Rebalancing" tip="On the first trading day of each month / quarter / year, overweight funds are sold and underweight funds bought to restore the target weights. Each switch-in pays stamp duty; the optional exit load applies to units sold within a year. Tax on the gains realised is NOT modelled." />
            <select value={rebalance} onChange={(e) => setRebalance(e.target.value as RebalanceFreq)} className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle}>
              {portfolio_sim_REBALANCE.map((r) => (
                <option key={r} value={r} className="bg-white text-slate-900">{r === "None" ? "None (buy and hold)" : r}</option>
              ))}
            </select>
          </label>
          <label className={labelClass}>
            <Label text="Benchmark" tip="The same money, on the same dates, into a comparison. Category peers: each fund replaced by the equal-weighted average of the Direct-Growth funds in its SEBI category AND asset class (so a Nifty index fund is compared with equity index funds, not gilt or Nasdaq ones; ETFs, which have one plan, count whatever AMFI's plan label says), at the same weights and rebalancing -- did your picks beat their peer groups? Segregated portfolios, daily-payout options labelled Growth and broken NAVs are left out, and funds that closed still count while they existed. A scheme: all of it into one fund you choose (e.g. an index fund), never rebalanced." />
            <select value={benchmark} onChange={(e) => setBenchmark(e.target.value as BenchmarkKind)} className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle}>
              <option value="category" className="bg-white text-slate-900">Category peers</option>
              <option value="scheme" className="bg-white text-slate-900">A specific scheme…</option>
              <option value="none" className="bg-white text-slate-900">None</option>
            </select>
          </label>
          {benchmark === "scheme" && (
            <div className={`${labelClass} md:col-span-2`}>
              <Label text="Benchmark scheme" tip="Pick any scheme, e.g. a Nifty 50 index fund's Direct-Growth option. Prefer a Growth option: an IDCW option's NAV drops at every payout." />
              <SearchCombobox placeholder={bench?.name ?? (benchCode ? `Scheme ${benchCode}` : "Search a benchmark fund (e.g. Nifty 50 index)…")}
                onSelect={(s) => setBenchCode(s.scheme_code)} />
              {bench?.name && <span style={muted}>Using: {bench.name}</span>}
            </div>
          )}
          <label className={labelClass}>
            <Label text="Exit load on rebalancing (%)" tip="Your assumption, applied to units sold by rebalancing within 365 days of purchase (oldest units sold first). This app has no per-scheme exit-load data: check the fund's Scheme Information Document -- many equity funds charge 1% within 12 months; liquid and index funds usually far less or none. 0 = none." />
            <input type="number" min={0} max={5} step={0.25} value={exitLoad} onChange={(e) => setExitLoad(Math.min(5, Math.max(0, Number(e.target.value))))}
              className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} />
          </label>
          <label className="flex items-center gap-2 text-xs font-medium" style={muted}>
            <input type="checkbox" checked={stampDuty} onChange={(e) => setStampDuty(e.target.checked)} />
            Apply stamp duty (0.005%)
            <Tip label="Stamp duty" text="0.005% of every purchase -- SIP instalments, lump sums and rebalancing switch-ins -- from 1 July 2020, deducted from the amount so fewer units are allotted. The Holdings ledger applies the same rule." />
          </label>
        </div>
      </section>

      {/* --- Status -------------------------------------------------------------------------- */}
      {!canRun ? (
        <Banner level="warning">
          {summary.sum <= 0 ? "Give at least one fund a positive weight." : !amountOk ? "Enter a positive amount." : "Select funds and a time horizon."}
        </Banner>
      ) : isError ? (
        <Banner level="danger">{(error as Error).message}</Banner>
      ) : data?.error ? (
        <Banner level="warning">{data.error}</Banner>
      ) : !ok ? (
        <p className="text-sm" style={muted}>Running backtest…</p>
      ) : null}

      {canRun && ok && tm && figs && (
        <>
          {(ok.warnings ?? []).map((wn, i) => (
            <Banner key={`${wn.code}-${i}`} level={wn.level === "warning" ? "warning" : "info"}>{wn.message}</Banner>
          ))}

          <p className="text-xs" style={muted}>
            {isFetching && <b style={{ color: "var(--mf-accent)" }}>Updating… </b>}
            Simulated {formatDate(w?.simulated_start ?? null)} to {formatDate(w?.simulated_end ?? null)} ({w?.span_days ?? 0} days)
            {" · "}{ok.n_contributions} contribution{ok.n_contributions === 1 ? "" : "s"}
            {a?.sip_day ? ` · SIP due on the ${a.sip_day === 31 ? "last day of each month" : `${ordinal(a.sip_day)} of each month`} (next NAV date if a holiday)` : ""}
            {" · "}Rebalancing: {rebalance === "None" ? "none" : rebalance.toLowerCase()}
          </p>

          {/* Money-weighted: what the investor's rupees did */}
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCard title="End value" value={formatInr(ok.final_value ?? 0)} sub={`Across ${ok.funds?.length ?? 0} fund(s)`}
              tooltip={<Tip label="End value" text="Units held in every fund on the last simulated day x that day's NAV. A NAV value, not a redemption payout: no exit load or tax is deducted from it." />} />
            <StatCard title="Money invested" value={formatInr(ok.total_invested ?? 0)} sub={`${ok.n_contributions ?? 0} contribution(s)`}
              tooltip={<Tip label="Money invested" text="Sum of your contributions (gross; stamp duty is taken out of them, not added on top). Rebalancing switches are internal and not counted." />} />
            <StatCard title="Gain" value={formatSignedInr(ok.absolute_gain ?? 0)} sub={`${formatSignedPct(ok.absolute_return_pct ?? null, 2)} on money invested (not annualised)`}
              tone={toneOf(ok.absolute_gain ?? null)}
              tooltip={<Tip label="Gain" formula="End value − money invested" text="Absolute rupee gain after stamp duty and exit loads. The percentage divides by the total invested, so it is not comparable across different SIP lengths -- use XIRR for that." />} />
            <StatCard title="XIRR (money-weighted)"
              value={ok.money_weighted_xirr_pct != null ? formatSignedPct(ok.money_weighted_xirr_pct, 2) : "—"}
              sub={ok.money_weighted_xirr_pct != null ? "Annual return on your actual cash flows" : xirrNoteText(ok.xirr_note, a?.min_annualise_days)}
              tone={toneOf(ok.money_weighted_xirr_pct ?? null)}
              tooltip={<Tip label="XIRR" formula="Σ Cᵢ / (1 + r)^(tᵢ / 365.25) = 0" text="The single annual rate that makes every contribution (−) and the end value (+) net to zero on their exact dates. It is what YOUR money earned, so it depends on when the SIP instalments went in. Uses a 365.25-day year (Excel uses 365). Withheld when the window is under 30 days." />} />
          </div>

          {/* Time-weighted: what the fund mix did */}
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCard title={withheld ? "TWR (not annualised)" : "TWR CAGR (time-weighted)"}
              value={formatSignedPct(withheld ? tm.total_return_pct ?? null : tm.cagr_pct ?? null, 2)}
              sub={withheld ? `Under ${a?.min_annualise_days ?? 30} days: not annualised` : `Total ${formatSignedPct(tm.total_return_pct ?? null, 2)} over the window`}
              tone={toneOf((withheld ? tm.total_return_pct : tm.cagr_pct) ?? null)}
              tooltip={<Tip label="Time-weighted return" formula="index_t = index_{t−1} · (pre_t / V_{t−1}) · (V_t / (pre_t + F_t)); CAGR = (index_end / 100)^(365.25 / days) − 1" text="How the fund mix performed with the size and timing of your contributions removed: each day's return is chain-linked, and a contribution day is split at the purchase so the new money never counts as a return (stamp duty and exit loads do). Directly comparable with a fund's or an index's CAGR. For a lump sum without costs it equals the XIRR." />} />
            <StatCard title="Volatility (ann.)" value={tm.vol_annualized_pct != null ? `${tm.vol_annualized_pct.toFixed(2)}%` : "—"}
              sub={`σ of TWR returns × √${(a?.obs_per_year ?? tm.obs_per_year ?? 0).toFixed(0)}`}
              tooltip={<Tip label="Annualised volatility" formula="σ(r_t − expected growth over r_t's own span) × √(NAV dates per year)" text={`Standard deviation of the portfolio's time-weighted returns from one valuation date to the next, each measured around the portfolio's average growth over that return's own number of calendar days (a Monday return covers three days of accrual; for debt and liquid funds that accrual is the return, not risk). Annualised on how often the portfolio is actually valued around this window (${(a?.obs_per_year ?? 0).toFixed(1)} dates a year here, counted from the data: ~250 for equity funds, more when a fund that also prices weekends, like a liquid fund, is in the mix) -- not a fixed √252.`} />} />
            <StatCard title="Max drawdown" value={tm.max_drawdown_pct != null ? `${tm.max_drawdown_pct.toFixed(2)}%` : "—"} tone={toneOf(tm.max_drawdown_pct ?? null)}
              sub="Deepest fall from a peak (TWR)"
              tooltip={<Tip label="Maximum drawdown" formula="min_t (index_t / max_{τ≤t} index_τ − 1)" text="The deepest peak-to-trough fall of the time-weighted index. Measured on the index, not on the rupee value, because SIP contributions would hide a fall in the rupee line. See the Drawdown chart." />} />
            <StatCard title="Sharpe ratio" value={tm.sharpe_ratio != null ? tm.sharpe_ratio.toFixed(2) : "—"}
              sub={`Excess over ${(tm.risk_free_rate_pct ?? 6.5).toFixed(1)}% risk-free, per unit of volatility`}
              tooltip={<Tip label="Sharpe ratio" formula="mean(r_t − r_f over r_t's own days) × N ÷ (σ × √N), N = valuation dates a year" text={`Average excess return per valuation interval over a ${(tm.risk_free_rate_pct ?? 6.5).toFixed(1)}% annual risk-free rate (the app-wide assumption) -- the risk-free return is charged for each interval's own calendar days, so a three-day weekend return has to beat three days of it -- scaled to a year and divided by the annualised volatility of the tile beside it. Noisy over short windows.`} />} />
          </div>

          {/* Benchmark and costs */}
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            {bench ? (
              <>
                <StatCard title="Benchmark end value" value={formatInr(bench.final_value ?? 0)}
                  sub={`You ${(ok.final_value ?? 0) >= (bench.final_value ?? 0) ? "beat" : "trailed"} it by ${formatInr(Math.abs((ok.final_value ?? 0) - (bench.final_value ?? 0)))}`}
                  tone={toneOf((ok.final_value ?? 0) - (bench.final_value ?? 0))}
                  tooltip={<Tip label="Benchmark end value" text={`${bench.name}: the same contributions on the same due dates${bench.kind === "category" ? ", the same weights and rebalancing," : " (each allotted at the benchmark's own next NAV)"} with the same stamp duty and exit-load rules. The difference is what your fund choice was worth in rupees.${bench.kind === "category" && (bench.components ?? []).some((c) => c.own_nav_used) ? " Where no peer series covers the whole window, that fund's own NAV stands in (see the note above), so that slice is neutral." : ""}`} />} />
                <StatCard title="Benchmark XIRR / TWR"
                  value={bench.money_weighted_xirr_pct != null ? formatSignedPct(bench.money_weighted_xirr_pct, 2) : xirrNoteText(bench.xirr_note, a?.min_annualise_days)}
                  sub={`TWR ${withheld ? "total" : "CAGR"} ${formatSignedPct((withheld ? bench.twr_total_return_pct : bench.twr_cagr_pct) ?? null, 2)} · max DD ${bench.max_drawdown_pct != null ? bench.max_drawdown_pct.toFixed(2) + "%" : "—"}`}
                  tooltip={<Tip label="Benchmark returns" text="The benchmark run's money-weighted XIRR (comparable with your XIRR tile) and time-weighted CAGR (comparable with your TWR CAGR tile), computed exactly the same way." />} />
              </>
            ) : (
              <StatCard title="Benchmark" value="—" sub={ok.benchmark?.error ?? "None selected"}
                tooltip={<Tip label="Benchmark" text="Choose Category peers or a specific scheme in the setup above to compare against the same cash flows." />} />
            )}
            <StatCard title="Stamp duty paid" value={formatInr(ok.costs?.stamp_duty ?? 0)}
              sub={a?.stamp_duty_applied ? `${a.stamp_duty_rate_pct.toFixed(3)}% of purchases from ${formatDate(a.stamp_duty_from)}` : "Switched off"}
              tooltip={<Tip label="Stamp duty" text="Total stamp duty deducted from contributions and rebalancing switch-ins (0.005% of each purchase on or after 1 July 2020)." />} />
            <StatCard title="Rebalancing" value={ok.costs?.rebalance_count ? `${ok.costs.rebalance_count} time(s)` : "None"}
              sub={ok.costs?.rebalance_count ? `${formatInr(ok.costs.rebalance_turnover)} sold · ${formatInr(ok.costs.rebalance_sold_under_exit_load_days)} of it held < 1 yr` : "Buy and hold"}
              tooltip={<Tip label="Rebalancing activity" text="How many rebalancing dates actually needed trades, the total value sold to restore the targets, and how much of that was units held under 365 days. Every sale is a redemption: in reality it can attract an exit load and capital-gains tax (short-term if held under a year for equity funds). Only your exit-load assumption is applied here." />} />
            <StatCard title="Exit loads paid" value={formatInr(ok.costs?.exit_load ?? 0)} sub={`${(a?.exit_load_pct ?? 0).toFixed(2)}% assumed on units < 1 yr`}
              tooltip={<Tip label="Exit loads" text="Your exit-load assumption applied to rebalancing sales of units held under 365 days, oldest units first. 0 unless you set one in the setup." />} />
          </div>

          {/* --- Charts ---------------------------------------------------------------------- */}
          <section>
            <div className="flex flex-wrap gap-1.5">
              {CHART_TABS.map((t) => (
                <button key={t.key} type="button" onClick={() => setChartTab(t.key)} className="rounded-full px-3 py-1.5 text-xs font-semibold"
                  style={{ background: chartTab === t.key ? "var(--mf-accent-bg)" : "transparent", color: chartTab === t.key ? "var(--mf-accent)" : "var(--mf-fg)" }}>
                  {t.label}
                </button>
              ))}
            </div>
            <div className="mt-2 flex items-center gap-1 text-xs" style={muted}>
              {chartTab === "value" && (<>Your portfolio&apos;s rupee value against the money put in{bench ? " and the benchmark bought with the same cash flows" : ""}. The gap to the dotted line is the gain.
                <Tip label="Value vs invested" text="Daily value = Σ units × NAV (a fund that did not publish that day is valued at its last NAV). The invested line steps up at every contribution." /></>)}
              {chartTab === "twr" && (<>Both lines start at 100 just before the first investment and move only with performance, not with contributions.
                <Tip label="Growth of 100" text="The chain-linked time-weighted index. The portfolio line starts a hair under 100 because day one already carries the entry stamp duty." /></>)}
              {chartTab === "drawdown" && (<>How far the time-weighted index sat below its previous peak on each day. 0% = at a new high.
                <Tip label="Drawdown" formula="index_t / max_{τ≤t} index_τ − 1" text="Measured on the TWR index so contributions cannot mask a fall." /></>)}
              {chartTab === "alloc" && (<>Each fund&apos;s share of the portfolio&apos;s value. Rebalancing (and each SIP instalment, which buys at the target weights) pulls it back towards target.
                <Tip label="Allocation drift" text="Holding value of each fund ÷ total portfolio value, per day. Colour follows the fund's position in your selection." /></>)}
              {chartTab === "rolling" && (<>
                Trailing {rollingYears}-year return of the TWR index at every date{rollingYears > 1 ? " (annualised)" : ""}.
                <Tip label="Rolling returns" text="index_t / index_(t − window) − 1, using the last value on or before the window start. Shows how much the result depended on the start date. Needs a window at least this long." />
                <span className="ml-2 inline-flex gap-1">
                  {([1, 3] as const).map((yv) => (
                    <button key={yv} type="button" onClick={() => setRollingYears(yv)} className="rounded-full border px-2 py-0.5"
                      style={{ borderColor: "var(--mf-border)", background: rollingYears === yv ? "var(--mf-accent-bg)" : "transparent", color: rollingYears === yv ? "var(--mf-accent)" : "var(--mf-fg)" }}>
                      {yv}Y
                    </button>
                  ))}
                </span></>)}
              {chartTab === "years" && (<>Time-weighted return in each calendar year. * = partial year (the window starts or ends inside it).
                <Tip label="Calendar-year returns" text="Previous year's last index value to this year's last. The table below adds each fund's own NAV return." /></>)}
            </div>
            <div className="mt-2">
              {chartTab === "value" && <PlotlyChart figure={figs.value} />}
              {chartTab === "twr" && <PlotlyChart figure={figs.twr} />}
              {chartTab === "drawdown" && <PlotlyChart figure={figs.drawdown} />}
              {chartTab === "alloc" && <PlotlyChart figure={figs.alloc} />}
              {chartTab === "rolling" && (figs.rolling.empty
                ? <p className="py-10 text-center text-sm" style={muted}>The simulated window is shorter than {rollingYears} year{rollingYears > 1 ? "s" : ""} -- nothing to roll.</p>
                : <PlotlyChart figure={figs.rolling} />)}
              {chartTab === "years" && <PlotlyChart figure={figs.years} />}
            </div>
          </section>

          {/* --- Per fund -------------------------------------------------------------------- */}
          <section>
            <h3 className="flex items-center gap-1 text-base font-bold">
              What each fund contributed
              <Tip label="Per-fund results" text="Each fund's share of the money, its rebalancing switches, what it was worth at the end and what it earned. Gains add up to the portfolio's gain exactly: stamp duty is charged to the fund that bought, exit loads to the fund that sold." />
            </h3>
            <div className="mt-2">
              <DataTable columns={fundColumns} rows={fundRows as unknown as Record<string, unknown>[]} keyField="scheme_code" footer={fundFooter} />
            </div>
          </section>

          {yearRows.length > 0 && (
            <section>
              <h3 className="flex items-center gap-1 text-base font-bold">
                Calendar-year returns
                <Tip label="Calendar-year returns" text="Portfolio and benchmark: time-weighted. Funds: their own NAV change. Years marked * are partial." />
              </h3>
              <div className="mt-2">
                <DataTable columns={yearColumns} rows={yearRows} keyField="label" />
              </div>
            </section>
          )}

          <details className="rounded-lg border p-3" style={{ borderColor: "var(--mf-border)" }}>
            <summary className="cursor-pointer text-sm font-bold">
              Transactions ({ok.trades?.length ?? 0}) -- every contribution and rebalancing switch
            </summary>
            <p className="mt-2 text-xs" style={muted}>
              The simulated order book: when each rupee went in, at which NAV, and what it cost.
            </p>
            <div className="mt-2 max-h-[480px] overflow-y-auto">
              <DataTable columns={tradeColumns} rows={(ok.trades ?? []).map((t, i) => ({ ...t, _k: i })) as unknown as Record<string, unknown>[]} keyField="_k" />
            </div>
          </details>

          <details className="rounded-lg border p-3" style={{ borderColor: "var(--mf-border)" }} open>
            <summary className="cursor-pointer text-sm font-bold">Assumptions</summary>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-xs" style={muted}>
              <li>Prices: official AMFI NAVs, which are already net of each fund&apos;s expense ratio (TER) -- nothing is deducted for it again. History is split-adjusted.</li>
              <li>Trade timing: orders are allotted only on dates on which every selected fund published its own NAV; a SIP date on a weekend or holiday rolls to the next such date. The value line still runs on days when only some funds priced (their last NAV is carried).</li>
              <li>Window: {formatDate(w?.simulated_start ?? null)} to {formatDate(w?.simulated_end ?? null)}. It cannot start before the youngest fund&apos;s first NAV or run past a fund&apos;s last one.</li>
              <li>Contributions: {a?.mode === "SIP (Monthly)" && a.sip_day ? `${formatInr(sipAmount)} a month, due on the ${a.sip_day === 31 ? "last day" : ordinal(a.sip_day)}${a.sip_day_defaulted ? " (the start date's day)" : ""}` : `${formatInr(lumpSum)} once, on the first day`}, split by the target weights.</li>
              <li>Stamp duty: {a?.stamp_duty_applied ? `${a.stamp_duty_rate_pct.toFixed(3)}% of every purchase and switch-in on or after ${formatDate(a.stamp_duty_from)}, taken out of the amount.` : "switched off."}</li>
              <li>Rebalancing: {rebalance === "None" ? "none (buy and hold; each SIP instalment still buys at the target weights)." : `${rebalance.toLowerCase()}, on the first trading day of the period; sells first-in-first-out.`} Exit load: {(a?.exit_load_pct ?? 0).toFixed(2)}% on units held under {a?.exit_load_days ?? 365} days (your assumption; no per-scheme exit-load data exists in this app).</li>
              <li>
                Tax: not modelled. Each rebalancing sale is a redemption and would be taxed. For context (check current rules): on redemptions from 23 July 2024,
                equity-oriented funds pay 20% on short-term gains (held up to 12 months) and 12.5% on long-term gains above ₹1.25 lakh a year; gains on
                debt funds bought on or after 1 April 2023 are taxed at your slab rate whatever the holding period.
              </li>
              <li>IDCW options: payouts are not added back, so an IDCW fund&apos;s return is understated by what it distributed. Prefer the Growth option.</li>
              <li>Units are not rounded to 3 decimals as an AMC does; minimum investment amounts are ignored.</li>
              <li>
                Returns: XIRR uses a 365.25-day year; XIRR and CAGR are withheld under {a?.min_annualise_days ?? 30} days. Volatility and Sharpe are annualised on{" "}
                {(a?.obs_per_year ?? 0).toFixed(1)} valuation dates a year (counted from these funds&apos; NAV dates around the window), with a {(a?.risk_free_rate_pct ?? 6.5).toFixed(1)}% risk-free rate
                charged over each return&apos;s own calendar days.
              </li>
              <li>
                Category-peer benchmark: each fund&apos;s slot follows the equal-weighted Direct-Growth funds of its SEBI category and asset class, averaged on their common
                calendar (a peer that did not price on a day waits at its last NAV). Segregated portfolios, &quot;Growth&quot; options whose NAV never moves, and broken NAVs
                (a spike that reverts the next day, a closed plan&apos;s re-based last NAV) are left out.
              </li>
              <li>Past returns do not predict future returns. This is an illustration, not advice.</li>
            </ul>
          </details>
        </>
      )}
    </div>
  );
}
