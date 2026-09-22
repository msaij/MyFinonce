import { describe, expect, it } from "vitest";

import type { Transaction } from "./api/holdings";
import {
  buildDraft,
  emptyForm,
  formFromTransaction,
  formatSignedInr,
  parseNumber,
  parsePortfolioKey,
  positionBadges,
  xirrReason,
  type TxnFormState,
} from "./holdings";

const base = (over: Partial<TxnFormState> = {}): TxnFormState => ({
  ...emptyForm(1, "2024-05-02"),
  schemeCode: 1001,
  value: "10000",
  ...over,
});

describe("buildDraft", () => {
  it("sends amount for an amount-mode purchase, units null, NAV omitted when blank", () => {
    const { draft, missing } = buildDraft(base());
    expect(missing).toBeNull();
    expect(draft).toMatchObject({ amount: 10000, units: null, nav: null, apply_stamp_duty: true, txn_type: "BUY" });
  });

  it("sends units in units mode and accepts Indian digit grouping and a rupee sign", () => {
    const { draft } = buildDraft(base({ mode: "units", value: "1,25,000.5" }));
    expect(draft).toMatchObject({ units: 125000.5, amount: null });
    expect(parseNumber("₹ 1,000")).toBe(1000);
  });

  it("explains what is missing instead of producing a half draft", () => {
    expect(buildDraft(base({ schemeCode: null })).missing).toBe("Choose a fund.");
    expect(buildDraft(base({ value: "" })).missing).toMatch(/amount or units/);
    expect(buildDraft(base({ txnType: "SWITCH" })).missing).toMatch(/switch into/);
    expect(buildDraft(base({ nav: "-3" })).missing).toMatch(/NAV must be/);
  });

  it("redeem-all needs no value and never sends one", () => {
    const { draft } = buildDraft(base({ txnType: "REDEEM", redeemAll: true, value: "" }));
    expect(draft).toMatchObject({ redeem_all: true, amount: null, units: null, apply_stamp_duty: false });
  });

  it("dividends are always an amount, whatever the mode toggle says", () => {
    const { draft } = buildDraft(base({ txnType: "DIVIDEND_PAYOUT", mode: "units", value: "250" }));
    expect(draft).toMatchObject({ amount: 250, units: null });
  });

  it("a switch carries its target and stamp duty (the switch-in leg is a purchase)", () => {
    const { draft } = buildDraft(base({ txnType: "SWITCH", switchTo: 2002, mode: "units", value: "10" }));
    expect(draft).toMatchObject({ switch_to_scheme_code: 2002, units: 10, apply_stamp_duty: true });
  });
});

describe("formFromTransaction", () => {
  const txn = (over: Partial<Transaction>): Transaction =>
    ({
      id: 7, portfolio_id: 1, scheme_code: 1001, txn_type: "BUY", trade_date: "2024-01-02",
      amount: "10000.00", units: "250.123000", nav: "39.9800", nav_source: "amfi_auto",
      stamp_duty: "0.50", switch_group: null, notes: null, deleted_at: null, ...over,
    }) as Transaction;

  it("re-enters a purchase by amount with AMFI NAV left blank", () => {
    const f = formFromTransaction(txn({}));
    expect(f).toMatchObject({ mode: "amount", value: "10000.00", nav: "", applyStampDuty: true });
  });

  it("re-enters a redemption by units and keeps a user NAV", () => {
    const f = formFromTransaction(txn({ txn_type: "REDEEM", nav_source: "user", stamp_duty: "0.00" }));
    expect(f).toMatchObject({ mode: "units", value: "250.123000", nav: "39.9800", applyStampDuty: false });
  });
});

describe("labels", () => {
  it("parses deep-link portfolio keys strictly", () => {
    expect(parsePortfolioKey("all")).toBe("all");
    expect(parsePortfolioKey("12")).toBe(12);
    expect(parsePortfolioKey("0")).toBeNull();
    expect(parsePortfolioKey("abc")).toBeNull();
    expect(parsePortfolioKey(null)).toBeNull();
  });

  it("signs rupee amounts so gains never rely on colour alone", () => {
    expect(formatSignedInr(1234.5)).toMatch(/^\+₹1,234\.5/);
    expect(formatSignedInr(-56.1)).toMatch(/^−₹56\.1/);
    expect(formatSignedInr(null)).toBe("-");
  });

  it("badges and XIRR reasons are human-readable", () => {
    expect(positionBadges({ is_closed: false, flags: { regular_plan: true, stale_nav: true, split_adjusted: false } })).toEqual([
      "Regular plan",
      "Stale NAV",
    ]);
    expect(xirrReason("too_short")).toMatch(/30 days/);
    expect(xirrReason(null)).toBe("");
  });
});
