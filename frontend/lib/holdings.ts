/**
 * Pure helpers for the Holdings page -- form -> API draft conversion and the
 * small labelling rules the tables share. No React here so Vitest can cover it.
 */

import type { MedianOutcome, PortfolioKey, Position, Transaction, TransactionDraft, TxnType } from "./api/holdings";
import { formatInrShort, formatInrWhole, formatSignedPct } from "./format";

export const TXN_TYPE_LABELS: Record<TxnType, string> = {
  BUY: "Purchase (lump sum)",
  SIP: "SIP instalment",
  REDEEM: "Redemption",
  SWITCH: "Switch to another fund",
  SWITCH_IN: "Switch in",
  SWITCH_OUT: "Switch out",
  DIVIDEND_REINVEST: "Dividend reinvested",
  DIVIDEND_PAYOUT: "Dividend paid out",
};

/** Types the add form offers (SWITCH_IN/OUT are created as a pair by SWITCH). */
export const FORM_TXN_TYPES: TxnType[] = ["BUY", "SIP", "REDEEM", "SWITCH", "DIVIDEND_REINVEST", "DIVIDEND_PAYOUT"];

export const PURCHASE_TYPES: TxnType[] = ["BUY", "SIP", "SWITCH_IN", "DIVIDEND_REINVEST"];
export const OUTFLOW_TYPES: TxnType[] = ["REDEEM", "SWITCH_OUT", "SWITCH"];

export type EntryMode = "amount" | "units";

export interface TxnFormState {
  portfolioId: number | null;
  schemeCode: number | null;
  txnType: TxnType;
  tradeDate: string;
  mode: EntryMode;
  value: string;
  nav: string;
  applyStampDuty: boolean;
  redeemAll: boolean;
  switchTo: number | null;
  notes: string;
}

export function emptyForm(portfolioId: number | null, today: string): TxnFormState {
  return {
    portfolioId,
    schemeCode: null,
    txnType: "BUY",
    tradeDate: today,
    mode: "amount",
    value: "",
    nav: "",
    applyStampDuty: true,
    redeemAll: false,
    switchTo: null,
    notes: "",
  };
}

/** Existing ledger row -> form (for edit). Purchases/dividends re-enter as the
 *  amount paid (units re-derive identically from the same NAV); redemptions as
 *  units, since that is what a redemption request specifies. */
export function formFromTransaction(t: Transaction): TxnFormState {
  const byUnits = OUTFLOW_TYPES.includes(t.txn_type);
  return {
    portfolioId: t.portfolio_id,
    schemeCode: t.scheme_code,
    txnType: t.txn_type,
    tradeDate: t.trade_date,
    mode: byUnits ? "units" : "amount",
    value: byUnits ? t.units : t.amount,
    nav: t.nav_source === "user" ? t.nav : "",
    applyStampDuty: Number(t.stamp_duty) > 0,
    redeemAll: false,
    switchTo: null,
    notes: t.notes ?? "",
  };
}

export function parseNumber(s: string): number | null {
  const cleaned = s.replace(/[,\s₹]/g, "");
  if (cleaned === "") return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

/** Form -> API draft, or a human reason it isn't complete yet. */
export function buildDraft(f: TxnFormState): { draft: TransactionDraft | null; missing: string | null } {
  if (f.portfolioId === null) return { draft: null, missing: "Choose a portfolio." };
  if (f.schemeCode === null) return { draft: null, missing: "Choose a fund." };
  if (!f.tradeDate) return { draft: null, missing: "Enter the trade date." };
  if (f.txnType === "SWITCH" && f.switchTo === null) return { draft: null, missing: "Choose the fund to switch into." };

  const isOutflow = OUTFLOW_TYPES.includes(f.txnType);
  const value = parseNumber(f.value);
  const redeemAll = isOutflow && f.redeemAll;
  if (!redeemAll && (value === null || value <= 0)) {
    return { draft: null, missing: f.txnType.startsWith("DIVIDEND") ? "Enter the dividend amount." : "Enter an amount or units." };
  }
  const nav = parseNumber(f.nav);
  if (f.nav.trim() !== "" && (nav === null || nav <= 0)) return { draft: null, missing: "NAV must be a positive number." };

  // Dividends are always entered as an amount.
  const mode: EntryMode = f.txnType.startsWith("DIVIDEND") ? "amount" : f.mode;
  const draft: TransactionDraft = {
    portfolio_id: f.portfolioId,
    scheme_code: f.schemeCode,
    txn_type: f.txnType,
    trade_date: f.tradeDate,
    amount: redeemAll ? null : mode === "amount" ? value : null,
    units: redeemAll ? null : mode === "units" ? value : null,
    nav: nav,
    apply_stamp_duty: PURCHASE_TYPES.includes(f.txnType) || f.txnType === "SWITCH" ? f.applyStampDuty : false,
    redeem_all: redeemAll,
    switch_to_scheme_code: f.txnType === "SWITCH" ? f.switchTo : null,
    notes: f.notes.trim() || null,
  };
  return { draft, missing: null };
}

export function parsePortfolioKey(raw: string | null): PortfolioKey | null {
  if (!raw) return null;
  if (raw === "all") return "all";
  const n = Number(raw);
  return Number.isInteger(n) && n > 0 ? n : null;
}

const XIRR_NOTES: Record<string, string> = {
  too_short: "Held under 30 days -- annualising would exaggerate",
  no_flows: "No cash flows yet",
  no_solution: "No stable rate for these cash flows",
};

export function xirrReason(note: string | null | undefined): string {
  return note ? XIRR_NOTES[note] ?? "Not available" : "";
}

/** Below this a gap to the benchmark is rounding, not a lead: 0.004 pp on a liquid fund
 *  is a single paisa of NAV, and calling it "ahead" would be reading noise as skill. */
const LEVEL_PP = 0.005;

/** "+0.06 pp ahead of peers" / "−0.12 pp behind peers" / "level with peers".
 *  Percentage points, not percent: the gap between two returns is a difference, and a
 *  relative figure ("50% better") would make tiny returns look dramatic. */
export function excessText(pp: number | null | undefined): string {
  if (pp === null || pp === undefined || Number.isNaN(pp)) return "";
  if (Math.abs(pp) < LEVEL_PP) return "level with peers";
  const mag = Math.abs(pp).toFixed(2);
  return pp > 0 ? `+${mag} pp ahead of peers` : `−${mag} pp behind peers`;
}

/** The tone a tile's comparison line should carry: being ahead of the benchmark is good
 *  news even in a falling market, so this follows the gap, never the return itself. */
export function excessTone(pp: number | null | undefined): "pos" | "neg" | "neutral" {
  if (pp === null || pp === undefined || Number.isNaN(pp) || Math.abs(pp) < LEVEL_PP) return "neutral";
  return pp > 0 ? "pos" : "neg";
}

/** "Shows from 15 Oct 2026" for an XIRR withheld as too short, else the plain reason. */
/** The hover breakdown for one fund's "If sold now": value, STT, what you get, what you
 *  paid, profit. null when there is no estimate (exchange-traded, or nothing held). */
export function sellNowBreakdown(p: Pick<Position, "current_value" | "latest_date" | "sell_now_value" | "sell_now_stt" | "sell_now_profit" | "total_invested">): string | null {
  if (p.sell_now_value == null || p.sell_now_profit == null) return null;
  const stt = p.sell_now_stt ?? 0;
  const inr = (v: number) => v.toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
  const asOf = p.latest_date ? ` (${p.latest_date})` : "";
  return (
    `Value at the latest NAV${asOf}: ${inr(p.current_value)}` +
    (stt > 0 ? ` − STT 0.001%: ${inr(stt)}` : " (no STT: not an equity-oriented fund)") +
    ` = you get ${inr(p.sell_now_value)}. You paid ${inr(p.total_invested)} (stamp duty included), so profit if sold: ${formatSignedInr(p.sell_now_profit)}.`
  );
}

/** The owner's reference rate for "Earning now", in % a year: at or above it reads green,
 *  below it red. */
export const EARNING_NOW_REFERENCE_PCT = 7;

/** How far from the reference (percentage points) the shade reaches full strength. */
export const EARNING_NOW_FULL_AT_PP = 3;

// A diverging scale: grey at the reference, red below, green above, deeper the further away.
// Light tints behind dark ink (the number keeps the text colour; the tint carries the
// position): ink contrast stays above 7:1 even at the poles.
const SHADE_NEUTRAL = [0xf0, 0xef, 0xec];
const SHADE_BELOW = [0xf0, 0x91, 0x89];
const SHADE_ABOVE = [0x86, 0xd1, 0xa2];

/** The background tint for an "Earning now" rate: grey at 7% a year, shading to full red
 *  at 4% or below and full green at 10% or above. null without a rate. */
export function earningNowShade(ratePct: number | null | undefined): string | null {
  if (ratePct === null || ratePct === undefined || Number.isNaN(ratePct)) return null;
  const gap = ratePct - EARNING_NOW_REFERENCE_PCT;
  const t = Math.min(Math.abs(gap) / EARNING_NOW_FULL_AT_PP, 1);
  const pole = gap >= 0 ? SHADE_ABOVE : SHADE_BELOW;
  const mix = SHADE_NEUTRAL.map((c, i) => Math.round(c + (pole[i] - c) * t));
  return `rgb(${mix.join(", ")})`;
}

/** A day's or a few days' move: three decimals below 0.1%, where a liquid portfolio's
 *  daily moves live and two decimals would print most of them as 0.00% or 0.01%. */
export function formatMovePct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return formatSignedPct(value, Math.abs(value) < 0.1 ? 3 : 2);
}

export function xirrPendingText(note: string | null | undefined, availableOn: string | null | undefined): string {
  if (note === "too_short" && availableOn) {
    const d = new Date(`${availableOn}T00:00:00`);
    if (!Number.isNaN(d.getTime())) {
      return `Shows from ${d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}`;
    }
  }
  return xirrReason(note);
}

/** Monte Carlo horizon bounds, in calendar days: one month to five years (the backend
 *  enforces the same range). */
export const MC_MIN_DAYS = 30;
export const MC_MAX_DAYS = 1826;
const DAYS_PER_MONTH = 365.25 / 12;

export function clampHorizonDays(d: number): number {
  if (!Number.isFinite(d)) return 365;
  return Math.min(MC_MAX_DAYS, Math.max(MC_MIN_DAYS, Math.round(d)));
}

/** The months slider and the days box drive one value; these convert between them. */
export function monthsToDays(months: number): number {
  return clampHorizonDays(months * DAYS_PER_MONTH);
}

export function daysToMonths(days: number): number {
  return Math.min(60, Math.max(1, Math.round(days / DAYS_PER_MONTH)));
}

/** "45 days" · "6 months (183 days)" · "2.5 years (913 days)" -- always with the exact
 *  day count once it is expressed in a coarser unit, so the chosen horizon is never vague. */
export function horizonLabel(days: number): string {
  if (days < 60) return `${days} days`;
  const months = days / DAYS_PER_MONTH;
  if (months < 23.5) return `${Math.round(months)} months (${days} days)`;
  const years = Math.round((days / 365.25) * 10) / 10;
  return `${Number.isInteger(years) ? years.toFixed(0) : years.toFixed(1)} years (${days} days)`;
}

/** The fan chart's x-axis unit, chosen so the axis reads naturally at any horizon:
 *  "day 0.8 of a year" is as unhelpful as "year 0.08" for a one-month run. */
export function horizonAxis(days: number): { unit: "Day" | "Month" | "Year"; perUnit: number; title: string } {
  if (days <= 92) return { unit: "Day", perUnit: 1, title: "Days from today" };
  if (days <= 730) return { unit: "Month", perUnit: DAYS_PER_MONTH, title: "Months from today" };
  return { unit: "Year", perUnit: 365.25, title: "Years from today" };
}

/** A window not yet available, stated as what it is waiting for. NAV days rather than a
 *  date, because market holidays make a date a promise the data might not keep. */
export function windowPendingText(daysNeeded: number): string {
  return daysNeeded > 0 ? `Needs ${daysNeeded} more NAV day${daysNeeded === 1 ? "" : "s"}` : "Not available";
}

export function formatUnits(n: number | string | null | undefined): string {
  const v = typeof n === "string" ? Number(n) : n;
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return v.toLocaleString("en-IN", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
}

/** Calendar days from the first purchase to the NAV date the position is valued at. */
export function heldDays(firstDate: string | null | undefined, asOf: string | null | undefined): number | null {
  if (!firstDate || !asOf) return null;
  const a = Date.parse(`${firstDate.slice(0, 10)}T00:00:00Z`);
  const b = Date.parse(`${asOf.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.max(0, Math.round((b - a) / 86_400_000));
}

/** "18 days" / "5 months" / "2.3 years": the unit a person would say it in. */
export function heldForText(days: number | null): string {
  if (days === null) return "";
  if (days < 60) return `${days} day${days === 1 ? "" : "s"}`;
  if (days < 730) return `${Math.floor(days / (365.25 / 12))} months`;
  return `${(days / 365.25).toFixed(1)} years`;
}

/** How long the money behind a gain has been invested: "~331 days" under a year, then
 *  "~1 yr 3 mo". The "~" because it is an average across purchases, not one date. */
export function investedForText(days: number | null | undefined): string {
  if (days == null || !Number.isFinite(days)) return "";
  const n = Math.round(days);
  if (n < 365) return `~${n} day${n === 1 ? "" : "s"}`;
  const months = Math.round(n / (365.25 / 12));
  const y = Math.floor(months / 12);
  const m = months % 12;
  return `~${y} yr${m ? ` ${m} mo` : ""}`;
}

export interface HoldingsTotals {
  invested: number;
  value: number;
  unrealised: number;
  /** On the cost of the units still held; null with nothing invested. */
  unrealisedPct: number | null;
  day: number;
  realised: number;
  weight: number;
  /** null when no row has a TER on record. */
  annualFee: number | null;
}

/** The Holdings table's totals row, summed over exactly the rows on screen. */
export function holdingsTotals(rows: Pick<Position, "cost_basis" | "current_value" | "unrealised_gain" | "day_change" | "realised_gain" | "weight_pct" | "annual_fee">[]): HoldingsTotals {
  const sum = (f: (r: (typeof rows)[number]) => number | null | undefined) => rows.reduce((a, r) => a + (f(r) ?? 0), 0);
  const invested = sum((r) => r.cost_basis);
  const unrealised = sum((r) => r.unrealised_gain);
  const fees = rows.filter((r) => r.annual_fee !== null && r.annual_fee !== undefined);
  return {
    invested,
    value: sum((r) => r.current_value),
    unrealised,
    unrealisedPct: invested > 0 ? (unrealised / invested) * 100 : null,
    day: sum((r) => r.day_change),
    realised: sum((r) => r.realised_gain),
    weight: sum((r) => r.weight_pct),
    annualFee: fees.length ? fees.reduce((a, r) => a + (r.annual_fee as number), 0) : null,
  };
}

/** Signed rupee string that never relies on colour alone (WCAG): "+₹1,234.00" / "−₹56.10". */
/** A goal's time left: "4.8 months" under a year, else "2 yrs 3 months". */
export function goalSpanText(months: number): string {
  if (months < 12) return `${months.toFixed(1)} month${months.toFixed(1) === "1.0" ? "" : "s"}`;
  let years = Math.floor(months / 12);
  let rest = Math.round(months - years * 12);
  if (rest === 12) {
    years += 1;
    rest = 0;
  }
  return `${years} yr${years === 1 ? "" : "s"}${rest ? ` ${rest} month${rest === 1 ? "" : "s"}` : ""}`;
}

/** The Median outcome tile's two lines: the gain from today over the time left, then the
 *  yearly rate and the gap to the target. With money still to invest, the gain is shown on
 *  that whole amount ("on ₹62.9L invested"), so SIP instalments never read as profit. */
export function medianOutcomeText(
  m: Pick<MedianOutcome, "money_in" | "still_to_invest" | "gain" | "gain_pct" | "rate_pct" | "vs_target">,
  months: number,
) {
  const signed = (v: number) => `${v < 0 ? "−" : "+"}${formatInrWhole(Math.abs(v))}`;
  const pct = m.gain_pct !== null ? formatSignedPct(m.gain_pct, 1) : null;
  const base = m.still_to_invest > 0 ? ` on ${formatInrShort(m.money_in)} invested` : "";
  return {
    gain: `${signed(m.gain)}${base}${pct ? ` (${pct})` : ""} in ${goalSpanText(months)}`,
    gainTone: (m.gain < 0 ? "neg" : "pos") as "pos" | "neg",
    rate: m.rate_pct !== null ? `≈ ${m.rate_pct.toFixed(1)}% a year` : null,
    target: m.vs_target >= 0 ? `${formatInrShort(m.vs_target)} above` : `${formatInrShort(-m.vs_target)} short`,
    targetTone: (m.vs_target >= 0 ? "pos" : "neg") as "pos" | "neg",
  };
}

export function formatSignedInr(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  const abs = Math.abs(v).toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
  return v > 0 ? `+${abs}` : v < 0 ? `−${abs}` : abs;
}

/** Expense ratios print to exactly 4 decimals and never carry a sign: AMFI publishes TER to
 *  4 places, and a fee is a cost, so "+0.45%" would read as a gain. Same rule as DataTable's
 *  "pct" format -- every TER on the page (the fund's own, its breakdown, its peer spread)
 *  goes through here so the same number never appears at two precisions on one screen. */
export function formatTer(v: number | string | null | undefined): string {
  const n = typeof v === "string" ? Number(v) : v;
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  return n.toFixed(4);
}

export function positionBadges(p: Pick<Position, "flags" | "is_closed">): string[] {
  const out: string[] = [];
  if (p.is_closed) out.push("Closed");
  if (p.flags.regular_plan) out.push("Regular plan");
  if (p.flags.stale_nav) out.push("Stale NAV");
  if (p.flags.split_adjusted) out.push("Split-adjusted");
  return out;
}

export interface FundTxnGroup {
  scheme_code: number;
  name: string;
  transactions: Transaction[];
  paid_in: number;
  taken_out: number;
  last_date: string;
}

/** Ledger -> one group per fund, labelled with the app-wide "Name (Plan - Option) [AMFI code]".
 *  Newest transaction first inside each group, and the fund with the most recent activity
 *  first. Switch legs land in their own fund's group. */
export function groupTransactionsByFund(txns: Transaction[]): FundTxnGroup[] {
  const groups = new Map<number, FundTxnGroup>();
  for (const t of txns) {
    let g = groups.get(t.scheme_code);
    if (!g) {
      g = { scheme_code: t.scheme_code, name: t.display_name ?? `Scheme ${t.scheme_code}`, transactions: [], paid_in: 0, taken_out: 0, last_date: t.trade_date };
      groups.set(t.scheme_code, g);
    }
    g.transactions.push(t);
    if (PURCHASE_TYPES.includes(t.txn_type)) g.paid_in += Number(t.amount);
    else if (OUTFLOW_TYPES.includes(t.txn_type) || t.txn_type === "DIVIDEND_PAYOUT") g.taken_out += Number(t.amount);
    if (t.trade_date > g.last_date) g.last_date = t.trade_date;
  }
  const byNewest = (a: Transaction, b: Transaction) => b.trade_date.localeCompare(a.trade_date) || b.id - a.id;
  return [...groups.values()]
    .map((g) => ({ ...g, transactions: [...g.transactions].sort(byNewest) }))
    .sort((a, b) => b.last_date.localeCompare(a.last_date) || a.name.localeCompare(b.name));
}

export function todayIso(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
