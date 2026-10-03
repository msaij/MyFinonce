"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";

import { AllocationPanel } from "@/components/holdings/AllocationPanel";
import { InsightsPanel } from "@/components/holdings/InsightsPanel";
import { PerformancePanel } from "@/components/holdings/PerformancePanel";
import { PlanningPanel } from "@/components/holdings/PlanningPanel";
import { PositionDrawer } from "@/components/holdings/PositionDrawer";
import { RiskPanel } from "@/components/holdings/RiskPanel";
import { TransactionDrawer } from "@/components/holdings/TransactionDrawer";
import { TransactionsTable } from "@/components/holdings/TransactionsTable";
import { AppShell } from "@/components/layout/AppShell";
import { Banner } from "@/components/shared/Banner";
import { DataTable, type ColumnConfig } from "@/components/shared/DataTable";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { StatCard } from "@/components/shared/StatCard";
import { RiskBadge } from "@/components/shared/RiskBadge";
import {
  archivePortfolio,
  backupUrl,
  createPortfolio,
  deleteTransaction,
  exportCsvUrl,
  getHoldingsSummary,
  getRecentChanges,
  listPortfolios,
  listTransactions,
  restoreBackup,
  restoreTransaction,
  updatePortfolio,
  type PortfolioKey,
  type Position,
  type Transaction,
} from "@/lib/api/holdings";
import { formatDate, formatInr, formatSignedPct, toneOf } from "@/lib/format";
import { useUrlSync } from "@/lib/hooks";
import {
  excessText,
  excessTone,
  formatSignedInr,
  formatMovePct,
  formatTer,
  formatUnits,
  groupTransactionsByFund,
  heldDays,
  heldForText,
  holdingsTotals,
  investedForText,
  parsePortfolioKey,
  positionBadges,
  windowPendingText,
  xirrPendingText,
  xirrReason,
} from "@/lib/holdings";

const TABS = ["overview", "holdings", "allocation", "risk", "insights", "plan", "transactions"] as const;
type Tab = (typeof TABS)[number];
const DEFAULT_TAB: Tab = "overview";
const TAB_LABELS: Record<Tab, string> = {
  overview: "Performance",
  holdings: "Holdings",
  allocation: "Allocation",
  risk: "Risk",
  insights: "Insights & alerts",
  plan: "Goals & SIPs",
  transactions: "Transactions",
};

const btn: React.CSSProperties = { borderColor: "var(--mf-border)", color: "var(--mf-fg)" };

export default function HoldingsPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm" style={{ color: "var(--mf-muted)" }}>Loading holdings…</div>}>
      <HoldingsContent />
    </Suspense>
  );
}

function HoldingsContent() {
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const [pid, setPid] = useState<PortfolioKey>(() => parsePortfolioKey(searchParams.get("portfolio")) ?? "all");
  const [tab, setTab] = useState<Tab>(() => (TABS.includes(searchParams.get("tab") as Tab) ? (searchParams.get("tab") as Tab) : DEFAULT_TAB));
  const [showClosed, setShowClosed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<Transaction | null>(null);
  const [prefill, setPrefill] = useState<{ code: number; name: string } | null>(null);
  const [detailCode, setDetailCode] = useState<number | null>(null);
  const [toast, setToast] = useState<{ text: string; undoId?: number } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useUrlSync({ portfolio: pid === "all" ? "all" : pid, tab: tab !== DEFAULT_TAB ? tab : undefined });

  const portfoliosQ = useQuery({ queryKey: ["holdings", "portfolios"], queryFn: () => listPortfolios(true) });
  const portfolios = portfoliosQ.data ?? [];
  const active = portfolios.filter((p) => !p.archived);
  const current = pid === "all" ? null : portfolios.find((p) => p.id === pid) ?? null;

  // A deep link to a portfolio that no longer exists falls back to the household view.
  useEffect(() => {
    if (portfoliosQ.isSuccess && pid !== "all" && !portfolios.some((p) => p.id === pid)) setPid("all");
  }, [portfoliosQ.isSuccess, portfolios, pid]);

  const summaryQ = useQuery({
    queryKey: ["holdings", "summary", pid],
    queryFn: () => getHoldingsSummary(pid),
    enabled: portfoliosQ.isSuccess && (pid === "all" || portfolios.some((p) => p.id === pid)),
  });
  const recentQ = useQuery({
    queryKey: ["holdings", "recent-changes", pid],
    queryFn: () => getRecentChanges(pid),
    enabled: summaryQ.isSuccess,
  });
  const txnsQ = useQuery({
    queryKey: ["holdings", "transactions", pid],
    queryFn: () => listTransactions(pid),
    enabled: tab === "transactions" && summaryQ.isSuccess,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["holdings"] });

  const del = useMutation({
    mutationFn: (id: number) => deleteTransaction(id),
    onSuccess: (res, id) => {
      invalidate();
      setToast({ text: res.deleted_ids.length > 1 ? "Switch (both legs) deleted." : "Transaction deleted.", undoId: id });
    },
    onError: (e: Error) => setActionError(e.message),
  });
  const undo = useMutation({
    mutationFn: (id: number) => restoreTransaction(id),
    onSuccess: () => {
      invalidate();
      setToast({ text: "Restored." });
    },
    onError: (e: Error) => setActionError(e.message),
  });

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), toast.undoId ? 10_000 : 3_000);
    return () => clearTimeout(t);
  }, [toast]);

  const openAdd = (scheme?: { code: number; name: string }) => {
    setEditing(null);
    setPrefill(scheme ?? null);
    setDrawerOpen(true);
  };

  const summary = summaryQ.data;
  const k = summary?.kpis;
  const positions = useMemo(
    () => (summary?.positions ?? []).filter((p) => showClosed || !p.is_closed),
    [summary, showClosed]
  );
  const txnGroups = useMemo(() => groupTransactionsByFund(txnsQ.data ?? []), [txnsQ.data]);
  const closedCount = (summary?.positions ?? []).filter((p) => p.is_closed).length;
  const noPortfolios = portfoliosQ.isSuccess && active.length === 0;
  const emptyLedger = summary && summary.kpis.transactions === 0;

  const pfName = useMemo(() => new Map(portfolios.map((p) => [p.id, p.name])), [portfolios]);
  const holdingsColumns: ColumnConfig[] = useMemo(
    () => [
      {
        key: "scheme_name",
        label: "Fund",
        tooltip: "Click a fund for its lots, transactions, NAV chart and expense-ratio detail. Under the name: its SEBI category, the asset class it counts toward on the Allocation tab, and its SEBI riskometer level.",
        sortValue: (r) => r.display_name,
        render: (row) => {
          const p = row as unknown as Position;
          const where = pid === "all" && p.portfolio_ids.length > 0 ? p.portfolio_ids.map((i) => pfName.get(i) ?? `#${i}`).join(", ") : null;
          return (
            <div className="min-w-[16rem]">
              <button type="button" className="text-left font-semibold hover:underline" style={{ color: "var(--mf-accent)" }} onClick={() => setDetailCode(p.scheme_code)}>
                {p.display_name}
              </button>
              <div className="text-[0.7rem]" style={{ color: "var(--mf-muted)" }} title={p.category ?? undefined}>
                {[p.sebi_category ?? p.category, p.asset_class].filter(Boolean).join(" · ")}
                {p.riskometer && (
                  <>
                    {" · "}
                    <RiskBadge level={p.riskometer} />
                  </>
                )}
                {where && <> · In {where}</>}
              </div>
              {positionBadges(p).length > 0 && (
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {positionBadges(p).map((b) => (
                    <span key={b} className="rounded border px-1 text-[0.65rem] font-semibold" style={{ borderColor: "var(--mf-border)", color: b === "Stale NAV" || b === "Regular plan" ? "var(--mf-warning)" : "var(--mf-muted)" }}>
                      {b}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        },
      },
      {
        key: "units", label: "Units", sortValue: (r) => r.units, render: (_r, v) => formatUnits(v as number),
        tooltip: "Units you hold now, to the 3 decimals AMCs allot. After a unit split they are shown on AMFI's post-split scale.",
      },
      {
        key: "avg_cost_nav", label: "Avg cost NAV", format: "number", decimals: 4,
        tooltip: "Invested ÷ units: the average NAV you paid for the units you still hold (first in, first out, so redeemed lots no longer count).",
      },
      {
        key: "latest_date", label: "NAV date",
        sortValue: (r) => (r.latest_date ? String(r.latest_date) : ""),
        render: (_r, v) => (v ? formatDate(v as string) : "-"),
        tooltip: "The date of the NAV this row is valued at: the fund's latest on AMFI. Funds publish on different days (most skip weekends and holidays; liquid funds publish every day), so the dates can differ between rows.",
      },
      {
        key: "latest_nav", label: "NAV", render: (_r, v) => (v == null ? "-" : Number(v).toFixed(4)),
        tooltip: "AMFI's latest published NAV for the fund, as of the NAV date beside it.",
      },
      {
        key: "cost_basis", label: "Invested", format: "inr",
        tooltip: "What the units you still hold cost, first in first out, net of stamp duty. Money from units already redeemed is in Realised, not here.",
      },
      { key: "current_value", label: "Value", format: "inr", tooltip: "Units × latest NAV." },
      {
        key: "unrealised_gain",
        label: "Unrealised",
        tooltip: "Value − Invested, on the units you still hold. The % is on Invested. It is a paper gain until you redeem.",
        sortValue: (r) => r.unrealised_gain,
        render: (r, v) => (
          <span className={toneOf(v as number) === "pos" ? "mf-pos" : toneOf(v as number) === "neg" ? "mf-neg" : ""}>
            {formatSignedInr(v as number)}
            <span className="block text-[0.7rem]">{formatSignedPct(r.unrealised_pct as number | null, 2)}</span>
          </span>
        ),
      },
      {
        key: "day_change",
        label: "1D gain",
        tooltip: "Rupees gained on the fund's latest NAV move: units held at the previous close × (latest NAV − previous NAV). Units allotted at the latest NAV have not moved yet, so they add nothing; a liquid fund bought on the latest date is allotted at the previous day's NAV and has earned the day. The % underneath is that move on what those units were worth before it, so funds of different sizes compare directly. The total matches the 1-day change tile.",
        sortValue: (r) => r.day_change,
        render: (r, v) => {
          const base = r.day_base as number | undefined;
          const from = r.prev_nav_date as string | null | undefined;
          const to = r.latest_date as string | null | undefined;
          // A fund that skipped publishing (a holiday, or a late NAV) moves over several days at
          // once; saying so keeps a three-day move from being compared as one day's.
          const span = from && to ? Math.round((Date.parse(to) - Date.parse(from)) / 86_400_000) : 1;
          return (
            <span
              className={toneOf(v as number) === "pos" ? "mf-pos" : toneOf(v as number) === "neg" ? "mf-neg" : ""}
              title={from && to ? `NAV move from ${formatDate(from)} to ${formatDate(to)}` : undefined}
            >
              {formatSignedInr(v as number)}
              {base ? (
                <span className="block text-[0.7rem]">
                  {formatMovePct(((v as number) / base) * 100)}
                  {span > 1 && <span style={{ color: "var(--mf-muted)" }}> · {span} days</span>}
                </span>
              ) : null}
            </span>
          );
        },
      },
      {
        key: "xirr_pct",
        label: "XIRR",
        tooltip: "Your annualised return in this fund, counting when each rupee went in and came out. Withheld for the first 30 days, because annualising a few days' move exaggerates it; the date it will show is given instead.",
        sortValue: (r) => r.xirr_pct,
        render: (r, v) =>
          v == null ? (
            <span className="text-xs" style={{ color: "var(--mf-muted)" }} title={xirrReason(r.xirr_note as string | null)}>
              {r.xirr_note === "too_short" && r.xirr_available_on ? `from ${formatDate(r.xirr_available_on as string)}` : "—"}
            </span>
          ) : (
            formatSignedPct(v as number, 2)
          ),
      },
      // No Realised column: the owner does not record redemptions -- a fund sold is simply
      // removed from the ledger -- so it would only ever read zero.
      {
        key: "weight_pct", label: "Weight", render: (_r, v) => (v == null ? "-" : `${(v as number).toFixed(1)}%`),
        tooltip: "This fund's share of the portfolio's value today.",
      },
      {
        key: "expense_ratio", label: "TER %",
        tooltip: "Total expense ratio exactly as AMFI publishes it: the yearly fee the fund deducts from its NAV. Hover a value for its source status. Total row: the value-weighted average across your funds.",
        render: (r, v) => (v == null ? "-" : <span title={String(r.ter_status ?? "")}>{formatTer(v as number)}</span>),
      },
      {
        key: "annual_fee", label: "Yearly fee",
        tooltip: "Value × TER: roughly what the expense ratio costs you over a year at today's value. It is taken out of the NAV daily, so it is already inside every return shown here. It is not a separate bill.",
        render: (_r, v) => (v == null ? "-" : formatInr(v as number)),
      },
      {
        key: "first_date", label: "Held for",
        tooltip: "Time since your first purchase in this fund, up to its latest NAV date. Useful for exit loads, and for the holding periods that set how gains are taxed.",
        sortValue: (r) => heldDays(r.first_date as string | null, r.latest_date as string | null),
        render: (r) => {
          const days = heldDays(r.first_date as string | null, r.latest_date as string | null);
          return days === null ? "-" : <span title={`First purchase ${formatDate(r.first_date as string)}`}>{heldForText(days)}</span>;
        },
      },
    ],
    [pid, pfName]
  );
  const totals = useMemo(() => holdingsTotals(positions), [positions]);
  const dayBase = useMemo(() => positions.reduce((s, p) => s + (p.day_base ?? 0), 0), [positions]);
  const holdingsFooter: Record<string, React.ReactNode> | undefined = k
    ? {
        scheme_name: `Total (${positions.length} fund${positions.length === 1 ? "" : "s"})`,
        cost_basis: formatInr(totals.invested),
        current_value: formatInr(totals.value),
        unrealised_gain: (
          <span className={toneOf(totals.unrealised) === "pos" ? "mf-pos" : toneOf(totals.unrealised) === "neg" ? "mf-neg" : ""}>
            {formatSignedInr(totals.unrealised)}
            <span className="block text-[0.7rem]">{formatSignedPct(totals.unrealisedPct, 2)}</span>
          </span>
        ),
        day_change: (
          <span className={toneOf(totals.day) === "pos" ? "mf-pos" : toneOf(totals.day) === "neg" ? "mf-neg" : ""}>
            {formatSignedInr(totals.day)}
            {dayBase > 0 && <span className="block text-[0.7rem]">{formatMovePct((totals.day / dayBase) * 100)}</span>}
          </span>
        ),
        xirr_pct:
          k.xirr_pct !== null ? (
            <span title="The whole portfolio's XIRR, closed funds included">{formatSignedPct(k.xirr_pct, 2)}</span>
          ) : (
            <span className="text-xs font-medium" style={{ color: "var(--mf-muted)" }}>{xirrPendingText(k.xirr_note, k.xirr_available_on)}</span>
          ),
        weight_pct: `${totals.weight.toFixed(1)}%`,
        expense_ratio:
          k.weighted_ter_pct !== null ? (
            <span title={`Value-weighted over ${k.ter_coverage_pct?.toFixed(1)}% of the portfolio's value (the funds with a TER on record)`}>{formatTer(k.weighted_ter_pct)}</span>
          ) : "-",
        annual_fee: totals.annualFee !== null ? formatInr(totals.annualFee) : "-",
      }
    : undefined;

  return (
    <AppShell>
      <h1 className="mf-page-title">Holdings</h1>
      <p className="mf-page-caption">
        Your own mutual fund portfolios, valued daily at official AMFI NAVs. Every figure traces back to a transaction you entered.
      </p>

      <PortfolioBar pid={pid} onSelect={setPid} />

      {actionError && (
        <div className="mt-3">
          <Banner level="danger">{actionError}</Banner>
        </div>
      )}

      {noPortfolios ? (
        <EmptyStart />
      ) : (
        <>
          {summaryQ.isError && (
            <div className="mt-4">
              <Banner level="danger">{(summaryQ.error as Error).message}</Banner>
            </div>
          )}

          {k && !emptyLedger && (
            <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
              {/* Each fund is valued at its own latest NAV. On a day AMFI has priced only some
                  of them, say so rather than claim one date for all. */}
              <StatCard
                title="Current value"
                value={formatInr(k.current_value)}
                sub={
                  !summary?.as_of
                    ? ""
                    : k.oldest_nav_date && k.oldest_nav_date < summary.as_of
                      ? `Latest NAVs, ${formatDate(k.oldest_nav_date)} to ${formatDate(summary.as_of)}`
                      : `NAVs as of ${formatDate(summary.as_of)}`
                }
              />
              <StatCard
                title="Invested"
                value={formatInr(k.invested)}
                sub={`Across ${k.open_positions} fund${k.open_positions === 1 ? "" : "s"}${k.stamp_duty > 0 ? ` · + ${formatInr(k.stamp_duty)} stamp duty` : ""}`}
                tooltip={
                  <FormulaTooltip
                    label="Invested"
                    formula="Σ units × purchase NAV (units still held, FIFO)"
                    description="The value allotted to you, as on your AMC statement or Coin. Stamp duty is not included; it is shown under Total gain. Units already sold move to Realised gain."
                  />
                }
              />
              {/* Unrealised gain used to have its own tile. For a buy-and-hold ledger it
                  differs from Total gain only by stamp duty, so two tiles said one thing;
                  it now leads this tile's sub-line and the full split is in the tooltip. */}
              <StatCard
                title="Total gain"
                value={formatSignedInr(k.total_gain)}
                tone={toneOf(k.total_gain)}
                // Current value − Invested is before stamp duty; this tile is after it. Saying
                // so is what makes the three tiles add up (to AMC unit-rounding paise).
                sub={`${k.total_gain_pct !== null ? `${formatSignedPct(k.total_gain_pct, 2)}${k.avg_days_invested != null ? ` in ${investedForText(k.avg_days_invested)}` : ""}` : ""}${k.stamp_duty > 0 ? `${k.total_gain_pct !== null ? " · " : ""}after stamp duty` : ""}`}
                subTone={toneOf(k.total_gain_pct ?? k.total_gain)}
                tooltip={
                  <FormulaTooltip
                    label="Total gain"
                    formula="current value + money taken out − money put in"
                    description={`What you have gained on the cash you actually paid (${formatInr(k.net_contributed)} net). Made up of unrealised ${formatSignedInr(k.unrealised_gain)} + realised ${formatSignedInr(k.realised_gain)} + dividends ${formatInr(k.dividend_income)} − stamp duty ${formatInr(k.stamp_duty)}; the parts can differ by a few paise because AMCs round units to 3 decimals.${
                      k.avg_days_invested != null
                        ? ` Your money has been invested for ${Math.round(k.avg_days_invested).toLocaleString("en-IN")} days on average: each rupee counts from the day it went in (to the day it came back out, oldest first, if sold), weighted by amount; switches between your funds keep the original date.${
                            k.days_since_first_investment != null && k.first_investment_date
                              ? ` Your first investment was ${k.days_since_first_investment.toLocaleString("en-IN")} days ago, on ${formatDate(k.first_investment_date)}.`
                              : ""
                          }`
                        : ""
                    }`}
                  />
                }
              />
              <StatCard
                title="Return since start"
                value={k.twr_since_start_pct !== null ? formatSignedPct(k.twr_since_start_pct, 2) : "—"}
                tone={toneOf(k.twr_since_start_pct)}
                // Percentage points alone scored the Rs 1,500 opening fortnight like the Rs 53
                // lakh since; the rupee figure weighs each day by the money actually at work.
                sub={
                  [excessText(k.excess_since_start_pp), k.gain_vs_peers != null ? `${formatSignedInr(k.gain_vs_peers)} in rupees` : ""]
                    .filter(Boolean)
                    .join(" · ") || (k.first_investment_date ? `Time-weighted, since ${formatDate(k.first_investment_date)}` : "")
                }
                subTone={k.gain_vs_peers != null ? toneOf(k.gain_vs_peers) : excessTone(k.excess_since_start_pp)}
                tooltip={
                  <FormulaTooltip
                    label="Return since start"
                    formula={"Π (1 + rₜ) − 1,  rₜ = (Vₜ − Fₜ) / Vₜ₋₁ − 1"}
                    description={`How your funds have done since ${k.first_investment_date ? formatDate(k.first_investment_date) : "your first investment"}, with the timing of your deposits taken out, so it is directly comparable with a benchmark and is not annualised. Every day counts equally, however little was invested that day.${k.benchmark_since_start_pct !== null ? ` Over the same days your funds' peer groups returned ${formatSignedPct(k.benchmark_since_start_pct, 2)}` + (k.benchmark_name ? ` (${k.benchmark_name}).` : ".") : ""}${
                      k.gain_vs_peers != null && k.peer_gain != null
                        ? ` In rupees, where each day counts by the money invested: the same cash, paid in on the same dates (allotted at the same NAV date, stamp duty included), would have made ${formatSignedInr(k.peer_gain)} in the peers; you made ${formatSignedInr(k.peer_gain + k.gain_vs_peers)}.`
                        : ""
                    } XIRR, beside it, is how your money did given when you invested it.`}
                  />
                }
              />
              <StatCard
                title="XIRR"
                value={k.xirr_pct !== null ? formatSignedPct(k.xirr_pct, 2) : "—"}
                tone={toneOf(k.xirr_pct)}
                sub={k.xirr_pct === null ? xirrPendingText(k.xirr_note, k.xirr_available_on) : "Money-weighted, annualised"}
                tooltip={
                  <FormulaTooltip
                    label="XIRR"
                    formula="Σ CFᵢ / (1 + r)^(tᵢ / 365.25) = 0"
                    description="The annual rate that makes every rupee you put in and took out, plus today's value, net to zero. Switches between your own funds are internal and excluded. Withheld until your money has been invested for 30 days on average (weighted by amount, not counted from your first rupee), because annualising a few days' return turns small moves into absurd yearly rates. A new purchase pushes the date back."
                  />
                }
              />
              <StatCard
                title="1-day change"
                value={formatSignedInr(k.day_change)}
                tone={toneOf(k.day_change)}
                sub={`${formatMovePct(k.day_change_pct)}${k.day_benchmark_pct !== null ? ` · peers ${formatMovePct(k.day_benchmark_pct)}` : ""}`}
                subTone={toneOf(k.day_change_pct)}
                tooltip={
                  <FormulaTooltip
                    label="1-day change"
                    formula="Vₜ − Vₜ₋₁ − Fₜ"
                    description="The total of the 1D gain column in the Holdings table: each fund's own latest NAV move, on the units you held before it (units bought at the latest NAV have not moved yet). When AMFI has priced only some funds for the newest date, the others count their own latest move, so nothing already published is left out and no fund is counted as unchanged. The percentage is on what those units were worth before the move; peers are each fund's SEBI category over that same fund's move, weighted the same way."
                  />
                }
              />
            </div>
          )}

          {k && !emptyLedger && (recentQ.data?.windows?.length ?? 0) > 0 && (
            <div className="mt-3">
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                {recentQ.data!.windows.map((w) => (
                  <StatCard
                    key={w.days}
                    title={`Last ${w.days} days`}
                    value={w.available ? formatMovePct(w.change_pct) : "—"}
                    tone={w.available ? toneOf(w.change_pct) : ""}
                    // The peers' figure sits beside the rupees so the two percentages can be
                    // read against each other at a glance; the sub keeps the rupee gain's
                    // tone, because colouring a positive "+₹1,215" red for trailing the
                    // benchmark would contradict its own sign.
                    sub={
                      w.available
                        ? `${formatSignedInr(w.gain)}${w.benchmark_change_pct !== null ? ` · peers ${formatMovePct(w.benchmark_change_pct)}` : ""}`
                        : windowPendingText(w.days_needed)
                    }
                    subTone={w.available ? toneOf(w.gain) : "neutral"}
                    tooltip={
                      w.available ? (
                        <FormulaTooltip
                          label={`Last ${w.days} NAV days`}
                          description={`${w.start ? `From ${formatDate(w.start)}, time-weighted. ` : ""}Averages ${formatSignedPct(w.avg_daily_pct, 3)} a day, compounded.${w.excess_pp !== null ? ` ${excessText(w.excess_pp).replace(/^./, (c) => c.toUpperCase())} (${recentQ.data?.benchmark_name ?? "benchmark"}).` : ""}`}
                        />
                      ) : undefined
                    }
                  />
                ))}
              </div>
              <p className="mt-1 text-xs" style={{ color: "var(--mf-muted)" }}>
                Movement of the holdings over the last few NAV days, time-weighted — money paid in during a window is not counted as a
                gain, though its stamp duty counts against the window, as it does against Total gain; the peers pay the same.{" "}
                {recentQ.data?.benchmark_name && !recentQ.data.benchmark_name.startsWith("Category blend")
                  ? `“Peers” is your chosen benchmark, ${recentQ.data.benchmark_name}, over the same window.`
                  : "“Peers” is the same window for the average fund in each of your funds’ SEBI categories, weighted like your portfolio."}
                {recentQ.data?.as_of ? ` NAVs as of ${formatDate(recentQ.data.as_of)}.` : ""}
                {recentQ.data?.awaiting_funds && recentQ.data.latest_nav_date
                  ? ` ${recentQ.data.awaiting_funds} of your funds (${Math.round(recentQ.data.awaiting_value_pct ?? 0)}% of the money) have not published ${formatDate(recentQ.data.latest_nav_date)} yet; their move for it is added when AMFI does, and their peers skip it until then.`
                  : ""}
              </p>
            </div>
          )}

          <div className="mt-6 flex flex-wrap items-center gap-1.5 border-b pb-2" style={{ borderColor: "var(--mf-border)" }}>
            {TABS.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTab(t)}
                className="rounded-full px-3.5 py-1.5 text-xs font-bold"
                style={{
                  background: tab === t ? "var(--mf-accent)" : "transparent",
                  color: tab === t ? "#ffffff" : "var(--mf-fg)",
                  border: tab === t ? "1px solid var(--mf-accent)" : "1px solid var(--mf-border)",
                }}
              >
                {TAB_LABELS[t]}
              </button>
            ))}
            <div className="ml-auto flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => openAdd()}
                disabled={current?.archived}
                className="rounded-lg px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
                style={{ background: "var(--mf-accent)", color: "#fff" }}
              >
                + Add transaction
              </button>
              <a href={exportCsvUrl(pid)} className="rounded-lg border px-3 py-1.5 text-xs font-semibold" style={btn}>
                Export CSV
              </a>
              <a href={backupUrl} className="rounded-lg border px-3 py-1.5 text-xs font-semibold" style={btn} title="Every portfolio and transaction, for safe keeping">
                Download backup
              </a>
            </div>
          </div>

          {summaryQ.isLoading && <div className="mt-4 text-sm" style={{ color: "var(--mf-muted)" }}>Valuing your holdings…</div>}

          {emptyLedger && (
            <div className="mt-6 rounded-lg border p-6 text-center" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
              <div className="text-base font-bold">No transactions yet</div>
              <p className="mt-1 text-sm" style={{ color: "var(--mf-muted)" }}>
                Add your first purchase or SIP. Pick the fund and date. The NAV fills in from AMFI, and you can overwrite it with the one on your statement.
              </p>
              <button type="button" onClick={() => openAdd()} className="mt-3 rounded-lg px-4 py-2 text-sm font-semibold" style={{ background: "var(--mf-accent)", color: "#fff" }}>
                + Add your first holding
              </button>
            </div>
          )}

          {tab === "overview" && summary && !emptyLedger && <PerformancePanel pid={pid} />}

          {tab === "allocation" && summary && !emptyLedger && <AllocationPanel pid={pid} />}

          {tab === "risk" && summary && !emptyLedger && <RiskPanel pid={pid} />}

          {tab === "insights" && summary && !emptyLedger && <InsightsPanel pid={pid} portfolios={portfolios} onOpenScheme={setDetailCode} />}

          {tab === "plan" && summary && <PlanningPanel pid={pid} portfolios={portfolios} />}

          {tab === "holdings" && summary && !emptyLedger && (
            <div className="mt-4">
              {closedCount > 0 && (
                <label className="mb-2 flex items-center gap-2 text-xs" style={{ color: "var(--mf-muted)" }}>
                  <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} />
                  Show {closedCount} fully redeemed holding{closedCount === 1 ? "" : "s"}
                </label>
              )}
              <DataTable columns={holdingsColumns} rows={positions as unknown as Record<string, unknown>[]} keyField="scheme_code" footer={holdingsFooter} />
              <p className="mt-2 text-xs" style={{ color: "var(--mf-muted)" }}>
                Valued at each fund&apos;s latest AMFI NAV{summary.as_of ? ` (up to ${formatDate(summary.as_of)})` : ""}. The total row adds up the funds shown
                {closedCount > 0 && !showClosed ? "; fully redeemed funds are hidden, but their realised gains still count on the Performance tab" : ""}.
              </p>
            </div>
          )}

          {tab === "transactions" && !emptyLedger && (
            <div className="mt-4">
              {txnsQ.isLoading && <div className="text-sm" style={{ color: "var(--mf-muted)" }}>Loading ledger…</div>}
              {/* Without this the tab renders blank on a failed fetch, which reads as "no transactions". */}
              {txnsQ.isError && <Banner level="danger">{(txnsQ.error as Error).message}</Banner>}
              {txnsQ.data && (
                <TransactionsTable
                  groups={txnGroups}
                  portfolioName={pid === "all" ? (id) => portfolios.find((p) => p.id === id)?.name ?? `#${id}` : undefined}
                  onOpenFund={setDetailCode}
                  onEdit={(t) => { setEditing(t); setPrefill(null); setDrawerOpen(true); }}
                  onDelete={(t) => { setActionError(null); del.mutate(t.id); }}
                  deleting={del.isPending}
                />
              )}
            </div>
          )}

          <p className="mt-6 text-xs" style={{ color: "var(--mf-muted)" }}>
            Values use AMFI&apos;s closing NAV and ignore exit loads and taxes. Realised gain uses FIFO lots per portfolio as a performance measure, not a tax computation.
          </p>
        </>
      )}

      <TransactionDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        portfolios={portfolios}
        defaultPortfolioId={pid === "all" ? (active.length === 1 ? active[0].id : null) : pid}
        editing={editing}
        prefillScheme={prefill}
      />
      <PositionDrawer pid={pid} schemeCode={detailCode} onClose={() => setDetailCode(null)} />

      {toast && (
        <div className="fixed bottom-5 left-1/2 z-50 flex -translate-x-1/2 items-center gap-3 rounded-lg px-4 py-2 text-sm shadow-lg" role="status" style={{ background: "#0f172a", color: "#fff" }}>
          {toast.text}
          {toast.undoId && (
            <button type="button" className="font-bold underline" onClick={() => { undo.mutate(toast.undoId!); setToast(null); }}>
              Undo
            </button>
          )}
        </div>
      )}
    </AppShell>
  );
}

function PortfolioBar({ pid, onSelect }: { pid: PortfolioKey; onSelect: (p: PortfolioKey) => void }) {
  const queryClient = useQueryClient();
  const { data: portfolios = [] } = useQuery({ queryKey: ["holdings", "portfolios"], queryFn: () => listPortfolios(true) });
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);

  const current = pid === "all" ? null : portfolios.find((p) => p.id === pid) ?? null;
  const visible = portfolios.filter((p) => showArchived || !p.archived || p.id === pid);
  const done = () => {
    setCreating(false);
    setRenaming(false);
    setName("");
    setError(null);
    queryClient.invalidateQueries({ queryKey: ["holdings"] });
  };

  const create = useMutation({
    mutationFn: () => createPortfolio({ name: name.trim() }),
    onSuccess: (p) => {
      done();
      onSelect(p.id);
    },
    onError: (e: Error) => setError(e.message),
  });
  const rename = useMutation({ mutationFn: () => updatePortfolio(current!.id, { name: name.trim() }), onSuccess: done, onError: (e: Error) => setError(e.message) });
  const archive = useMutation({ mutationFn: () => archivePortfolio(current!.id), onSuccess: () => { done(); onSelect("all"); } });
  const unarchive = useMutation({ mutationFn: () => updatePortfolio(current!.id, { archived: false }), onSuccess: done });

  const pill = (active: boolean): React.CSSProperties => ({
    background: active ? "var(--mf-accent-bg)" : "transparent",
    color: active ? "var(--mf-accent)" : "var(--mf-fg)",
    border: `1px solid ${active ? "var(--mf-accent)" : "var(--mf-border)"}`,
  });

  return (
    <div className="mt-4 flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Portfolio">
        <button type="button" role="tab" aria-selected={pid === "all"} onClick={() => onSelect("all")} className="rounded-full px-3 py-1 text-xs font-semibold" style={pill(pid === "all")}>
          All portfolios
        </button>
        {visible.map((p) => (
          <button key={p.id} type="button" role="tab" aria-selected={pid === p.id} onClick={() => onSelect(p.id)} className="rounded-full px-3 py-1 text-xs font-semibold" style={{ ...pill(pid === p.id), opacity: p.archived ? 0.6 : 1 }}>
            {p.name}
            {p.archived ? " (archived)" : ""}
          </button>
        ))}
        {!creating && (
          <button type="button" onClick={() => { setCreating(true); setRenaming(false); setName(""); }} className="rounded-full px-3 py-1 text-xs font-semibold" style={{ color: "var(--mf-accent)", border: "1px dashed var(--mf-border)" }}>
            + New portfolio
          </button>
        )}
        {portfolios.some((p) => p.archived) && (
          <label className="ml-1 flex items-center gap-1 text-[0.7rem]" style={{ color: "var(--mf-muted)" }}>
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> archived
          </label>
        )}
        {current && !creating && !renaming && (
          <div className="ml-auto flex gap-3 text-xs font-semibold">
            <button type="button" style={{ color: "var(--mf-accent)" }} onClick={() => { setRenaming(true); setName(current.name); }}>
              Rename
            </button>
            {current.archived ? (
              <button type="button" style={{ color: "var(--mf-accent)" }} onClick={() => unarchive.mutate()}>
                Unarchive
              </button>
            ) : (
              <button type="button" style={{ color: "var(--mf-muted)" }} onClick={() => archive.mutate()} title="Hides it from the household view. Nothing is deleted.">
                Archive
              </button>
            )}
          </div>
        )}
      </div>
      {(creating || renaming) && (
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!name.trim()) return;
            (creating ? create : rename).mutate();
          }}
        >
          <input
            autoFocus
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
            placeholder={creating ? "e.g. Self, Spouse, Retirement" : ""}
            className="rounded-lg border px-2 py-1.5 text-sm"
            style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-fg)" }}
          />
          <button type="submit" className="rounded-lg px-3 py-1.5 text-xs font-semibold" style={{ background: "var(--mf-accent)", color: "#fff" }}>
            {creating ? "Create" : "Save"}
          </button>
          <button type="button" className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }} onClick={() => { setCreating(false); setRenaming(false); setError(null); }}>
            Cancel
          </button>
          {error && <span className="text-xs" style={{ color: "var(--mf-danger)" }}>{error}</span>}
        </form>
      )}
    </div>
  );
}

function EmptyStart() {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [msg, setMsg] = useState<{ level: "info" | "danger"; text: string } | null>(null);
  const restore = useMutation({
    mutationFn: async (file: File) => restoreBackup(JSON.parse(await file.text())),
    onSuccess: (r) => {
      setMsg({ level: "info", text: `Restored ${r.portfolios} portfolio(s) and ${r.holding_transactions} transaction(s).` });
      queryClient.invalidateQueries({ queryKey: ["holdings"] });
    },
    onError: (e: Error) => setMsg({ level: "danger", text: e.message }),
  });
  return (
    <div className="mt-6 rounded-lg border p-6" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
      <div className="text-base font-bold">Start tracking what you own</div>
      <p className="mt-1 text-sm" style={{ color: "var(--mf-muted)" }}>
        Create a portfolio above, one per person or goal (Self, Spouse, Retirement…). &quot;All portfolios&quot; then shows the whole household together.
      </p>
      <div className="mt-4 text-xs" style={{ color: "var(--mf-muted)" }}>
        Have a backup file from this app?{" "}
        <button type="button" className="font-semibold underline" style={{ color: "var(--mf-accent)" }} onClick={() => fileRef.current?.click()}>
          Restore it
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) restore.mutate(f);
            e.target.value = "";
          }}
        />
      </div>
      {msg && (
        <div className="mt-3">
          <Banner level={msg.level}>{msg.text}</Banner>
        </div>
      )}
    </div>
  );
}
