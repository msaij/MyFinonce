/**
 * Pure helpers for the Holdings page -- form -> API draft conversion and the
 * small labelling rules the tables share. No React here so Vitest can cover it.
 */

import type { PortfolioKey, Position, Transaction, TransactionDraft, TxnType } from "./api/holdings";

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

export function formatUnits(n: number | string | null | undefined): string {
  const v = typeof n === "string" ? Number(n) : n;
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return v.toLocaleString("en-IN", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
}

/** Signed rupee string that never relies on colour alone (WCAG): "+₹1,234.00" / "−₹56.10". */
export function formatSignedInr(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  const abs = Math.abs(v).toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
  return v > 0 ? `+${abs}` : v < 0 ? `−${abs}` : abs;
}

export function positionBadges(p: Pick<Position, "flags" | "is_closed">): string[] {
  const out: string[] = [];
  if (p.is_closed) out.push("Closed");
  if (p.flags.regular_plan) out.push("Regular plan");
  if (p.flags.stale_nav) out.push("Stale NAV");
  if (p.flags.split_adjusted) out.push("Split-adjusted");
  return out;
}

export function todayIso(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
