import { describe, expect, it } from "vitest";

import type { Transaction } from "./api/holdings";
import {
  MC_MAX_DAYS,
  MC_MIN_DAYS,
  buildDraft,
  clampHorizonDays,
  daysToMonths,
  emptyForm,
  excessText,
  horizonAxis,
  horizonLabel,
  monthsToDays,
  excessTone,
  formFromTransaction,
  formatSignedInr,
  formatTer,
  windowPendingText,
  xirrPendingText,
  groupTransactionsByFund,
  heldDays,
  heldForText,
  holdingsTotals,
  investedForText,
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

const txn = (over: Partial<Transaction>): Transaction =>
  ({
    id: 7, portfolio_id: 1, scheme_code: 1001, txn_type: "BUY", trade_date: "2024-01-02",
    amount: "10000.00", units: "250.123000", nav: "39.9800", nav_source: "amfi_auto",
    stamp_duty: "0.50", switch_group: null, notes: null, deleted_at: null, ...over,
  }) as Transaction;

describe("formFromTransaction", () => {
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

  it("prints every TER at 4 decimals, unsigned, whatever its magnitude", () => {
    expect(formatTer(0.09)).toBe("0.0900");
    expect(formatTer(3.83)).toBe("3.8300");
    // The peer spread is a TER too: it used to print at 2 decimals beside the same
    // number at 4, so one median read as both "1.47%" and "1.4650%".
    expect(formatTer(1.4649999999999999)).toBe("1.4650");
    expect(formatTer("0.2")).toBe("0.2000");
    expect(formatTer(0)).toBe("0.0000");
    expect(formatTer(null)).toBe("-");
    expect(formatTer(undefined)).toBe("-");
    // A fee never wears a "+" -- that would read as a gain.
    expect(formatTer(1.5).startsWith("+")).toBe(false);
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

describe("fund names and grouping", () => {
  it("groups by fund, newest first, with money in and out per fund", () => {
    const groups = groupTransactionsByFund([
      txn({ id: 1, scheme_code: 1, display_name: "A Fund (Direct - Growth) [1]", trade_date: "2024-01-01", amount: "100.00" }),
      txn({ id: 2, scheme_code: 2, scheme_name: "B Fund", trade_date: "2024-03-01", amount: "50.00" }),
      txn({ id: 3, scheme_code: 1, scheme_name: "A Fund", trade_date: "2024-02-01", txn_type: "SIP", amount: "20.00" }),
      txn({ id: 4, scheme_code: 1, scheme_name: "A Fund", trade_date: "2024-02-15", txn_type: "REDEEM", amount: "30.00" }),
    ]);
    expect(groups.map((g) => g.scheme_code)).toEqual([2, 1]);
    expect(groups[1].name).toBe("A Fund (Direct - Growth) [1]");
    expect(groupTransactionsByFund([txn({ scheme_code: 9 })])[0].name).toBe("Scheme 9");
    expect(groups[1].transactions.map((t) => t.id)).toEqual([4, 3, 1]);
    expect(groups[1]).toMatchObject({ paid_in: 120, taken_out: 30, last_date: "2024-02-15" });
  });
});
describe("Monte Carlo horizon", () => {
  it("spans exactly one month to five years, in calendar days", () => {
    expect(monthsToDays(1)).toBe(MC_MIN_DAYS);
    expect(monthsToDays(60)).toBe(MC_MAX_DAYS);
    expect(monthsToDays(12)).toBe(365);
  });

  it("clamps anything typed outside the range instead of sending it", () => {
    expect(clampHorizonDays(5)).toBe(30);
    expect(clampHorizonDays(99999)).toBe(1826);
    expect(clampHorizonDays(Number.NaN)).toBe(365);
    expect(clampHorizonDays(45.6)).toBe(46);
  });

  it("keeps the months slider in step with a typed day count", () => {
    expect(daysToMonths(45)).toBe(1);
    expect(daysToMonths(183)).toBe(6);
    expect(daysToMonths(1826)).toBe(60);
  });

  it("labels the horizon in a natural unit but always states the exact days", () => {
    expect(horizonLabel(45)).toBe("45 days");
    expect(horizonLabel(183)).toBe("6 months (183 days)");
    expect(horizonLabel(913)).toBe("2.5 years (913 days)");
    expect(horizonLabel(1826)).toBe("5 years (1826 days)");
  });

  it("picks an axis unit that reads naturally at any horizon", () => {
    expect(horizonAxis(45).unit).toBe("Day");
    expect(horizonAxis(365).unit).toBe("Month");
    expect(horizonAxis(1826).unit).toBe("Year");
  });
});

describe("Holdings table", () => {
  const row = (over: Partial<Record<string, number | null>>) => ({
    cost_basis: 1000, current_value: 1100, unrealised_gain: 100, day_change: 5, realised_gain: 0, weight_pct: 50, annual_fee: 1.1, ...over,
  }) as Parameters<typeof holdingsTotals>[0][number];

  it("totals exactly the rows shown, with the % on the cost of what is still held", () => {
    const t = holdingsTotals([row({}), row({ cost_basis: 3000, current_value: 2700, unrealised_gain: -300, day_change: -2, realised_gain: 40, annual_fee: 5.4 })]);
    expect(t).toMatchObject({ invested: 4000, value: 3800, unrealised: -200, day: 3, realised: 40, weight: 100 });
    expect(t.unrealisedPct).toBeCloseTo(-5);
    expect(t.annualFee).toBeCloseTo(6.5);
  });

  it("has no fee total when no fund has a TER, rather than a false zero", () => {
    expect(holdingsTotals([row({ annual_fee: null })]).annualFee).toBeNull();
    expect(holdingsTotals([]).unrealisedPct).toBeNull();
  });

  it("states the holding period in the unit a person would use", () => {
    expect(heldDays("2026-09-15", "2026-09-24")).toBe(9);
    expect(heldForText(1)).toBe("1 day");
    expect(heldForText(45)).toBe("45 days");
    expect(heldForText(200)).toBe("6 months");
    expect(heldForText(1000)).toBe("2.7 years");
    expect(heldDays(null, "2026-09-24")).toBeNull();
  });

  it("says how long the money behind a gain was invested", () => {
    expect(investedForText(330.75)).toBe("~331 days");
    expect(investedForText(1)).toBe("~1 day");
    expect(investedForText(456)).toBe("~1 yr 3 mo");
    expect(investedForText(730)).toBe("~2 yr");
    expect(investedForText(null)).toBe("");
  });
});

describe("KPI comparison wording", () => {
  it("states the gap to the benchmark in percentage points, with a direction", () => {
    expect(excessText(0.0612)).toBe("+0.06 pp ahead of peers");
    expect(excessText(-0.12)).toBe("−0.12 pp behind peers");
  });

  it("calls a sub-rounding gap level rather than reading noise as a lead", () => {
    expect(excessText(0.004)).toBe("level with peers");
    expect(excessTone(0.004)).toBe("neutral");
  });

  it("tones the comparison by the gap, not by the return, and says nothing without a benchmark", () => {
    expect(excessTone(0.2)).toBe("pos");
    expect(excessTone(-0.2)).toBe("neg");
    expect(excessText(null)).toBe("");
    expect(excessTone(undefined)).toBe("neutral");
  });

  it("promises a date for a withheld XIRR instead of repeating the reason", () => {
    expect(xirrPendingText("too_short", "2026-10-15")).toBe("Shows from 15 Oct 2026");
    expect(xirrPendingText("no_solution", null)).not.toMatch(/Shows from/);
  });

  it("counts down NAV days for a window that is not available yet", () => {
    expect(windowPendingText(2)).toBe("Needs 2 more NAV days");
    expect(windowPendingText(1)).toBe("Needs 1 more NAV day");
  });
});
