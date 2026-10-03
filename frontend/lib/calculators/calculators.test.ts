import { describe, expect, it } from "vitest";

import { applyKey, evaluate, formatNumber } from "./expression";

const press = (keys: string[], start = "", afterEquals = false) =>
  keys.reduce((cur, k, i) => applyKey(cur, k, afterEquals && i === 0), start);

describe("applyKey", () => {
  it("replaces a repeated operator but allows a negative after × or ÷", () => {
    expect(press(["5", "+", "×", "2"])).toBe("5×2");
    expect(press(["5", "×", "−", "2"])).toBe("5×−2");
  });
  it("allows one decimal point per number", () => {
    expect(press([".", "5", ".", "+", "."])).toBe("0.5+0.");
  });
  it("implies × before a bracket and only closes open brackets", () => {
    expect(press(["2", "(", "3", ")", ")"])).toBe("2×(3)");
  });
  it("after =, a digit starts afresh and an operator continues", () => {
    expect(press(["7"], "42", true)).toBe("7");
    expect(press(["+", "1"], "42", true)).toBe("42+1");
    expect(press(["±"], "42", true)).toBe("−42");
  });
  it("± toggles the sign of the number being typed", () => {
    expect(press(["5", "±"])).toBe("−5");
    expect(press(["5", "±", "±"])).toBe("5");
    expect(press(["5", "+", "3", "±"])).toBe("5+(−3)");
    expect(press(["5", "+", "3", "±", "±"])).toBe("5+3");
    expect(press(["5", "×", "3", "±"])).toBe("5×−3");
    expect(val(press(["5", "−", "3", "±"]))).toBe(8);
  });
  it("x² and √ build valid expressions", () => {
    expect(press(["9", "x²"])).toBe("9^2");
    expect(press(["2", "√", "9"])).toBe("2×√9");
  });
});

describe("quick percentages", () => {
  it("does the everyday sums", () => {
    expect(percentOf(18, 2500)).toBe(450);
    expect(percentChange(80, 100)).toBe(25);
    expect(percentChange(0, 5)).toBeNull();
    expect(percentShare(45, 180)).toBe(25);
  });
  it("adds or removes GST", () => {
    expect(gst(1000, 18, "add")).toEqual({ net: 1000, tax: 180, gross: 1180 });
    const r = gst(1180, 18, "remove");
    expect(r.net).toBeCloseTo(1000, 9);
    expect(r.tax).toBeCloseTo(180, 9);
  });
});

describe("powers and roots", () => {
  it("follows maths precedence", () => {
    expect(val("2^10")).toBe(1024);
    expect(val("−2^2")).toBe(-4);
    expect(val("2^3^2")).toBe(512);
    expect(val("2×3^2")).toBe(18);
    expect(val("√16+√9")).toBe(7);
    expect(val("√(9+16)")).toBe(5);
    expect(val("2^−1")).toBe(0.5);
  });
  it("rejects what has no real answer", () => {
    expect(evaluate("√−4")).toEqual({ ok: false, error: "No square root of a negative number" });
  });
});
import { gst, percentChange, percentOf, percentShare } from "./percent";
import { addMonths, durationLabel, emiSchedule, groupByYear, monthlyEmi } from "./emi";

const val = (s: string) => {
  const r = evaluate(s);
  if (!r.ok) throw new Error(r.error);
  return r.value;
};

describe("evaluate", () => {
  it("applies × ÷ before + − and honours brackets", () => {
    expect(val("2+3×4")).toBe(14);
    expect(val("(2+3)×4")).toBe(20);
    expect(val("10−4−3")).toBe(3);
    expect(val("100÷4÷5")).toBe(5);
    expect(val("-3×-2")).toBe(6);
  });
  it("hides floating-point noise", () => {
    expect(val("0.1+0.2")).toBe(0.3);
  });
  it("treats percent like a phone calculator", () => {
    expect(val("200+10%")).toBe(220);
    expect(val("200-10%")).toBe(180);
    expect(val("50×10%")).toBe(5);
    expect(val("10%")).toBe(0.1);
  });
  it("accepts typed operators and grouped numbers", () => {
    expect(val("1,00,000*2/4")).toBe(50000);
  });
  it("reports errors instead of throwing", () => {
    expect(evaluate("5÷0")).toEqual({ ok: false, error: "Can't divide by zero" });
    expect(evaluate("(2+3")).toEqual({ ok: false, error: "Missing )" });
    expect(evaluate("2+")).toEqual({ ok: false, error: "Incomplete expression" });
    expect(evaluate("1.2.3")).toEqual({ ok: false, error: "Invalid number" });
  });
  it("formats with Indian grouping", () => {
    expect(formatNumber(1234567.5)).toBe("12,34,567.5");
  });
});

describe("EMI", () => {
  it("matches the standard formula", () => {
    // Rs 10 lakh at 8.5% for 20 years: the published EMI is Rs 8,678.
    expect(monthlyEmi(1_000_000, 8.5, 240)).toBeCloseTo(8678.23, 2);
    expect(monthlyEmi(120_000, 0, 12)).toBe(10_000);
  });
  it("amortises to exactly zero and totals add up", () => {
    const r = emiSchedule(1_000_000, 8.5, 240, "2026-10");
    expect(r.rows).toHaveLength(240);
    expect(r.rows.at(-1)!.balance).toBe(0);
    expect(r.rows.reduce((s, x) => s + x.principal, 0)).toBeCloseTo(1_000_000, 6);
    expect(r.totalPayment).toBeCloseTo(r.emi * 240, 0);
    expect(r.rows[0].month).toBe("2026-10");
    expect(r.rows.at(-1)!.month).toBe("2046-09");
  });
  it("groups by calendar or financial (April-March) year", () => {
    const rows = emiSchedule(120_000, 10, 12, "2026-10").rows;
    expect(groupByYear(rows, "calendar").map((y) => [y.label, y.rows.length])).toEqual([["2026", 3], ["2027", 9]]);
    expect(groupByYear(rows, "financial").map((y) => [y.label, y.rows.length])).toEqual([["FY 2026-27", 6], ["FY 2027-28", 6]]);
  });
  it("steps months across year ends", () => {
    expect(addMonths("2026-11", 3)).toBe("2027-02");
  });
});

describe("prepayments", () => {
  const P = 2_500_000;
  const base = emiSchedule(P, 8.5, 240, "2026-10");
  const paidOff = (r: ReturnType<typeof emiSchedule>) => r.rows.reduce((s, x) => s + x.principal + x.prepayment, 0);

  it("a monthly extra shortens the loan and cuts interest, keeping the EMI", () => {
    const r = emiSchedule(P, 8.5, 240, "2026-10", { monthly: 5000 });
    expect(r.rows.length).toBeLessThan(240);
    expect(r.totalInterest).toBeLessThan(base.totalInterest);
    expect(r.rows[5].emi).toBeCloseTo(base.emi, 6);
    expect(paidOff(r)).toBeCloseTo(P, 4);
    expect(r.rows.at(-1)!.balance).toBe(0);
  });

  it("with the 'emi' effect, keeps the end date and lowers the EMI after a prepayment", () => {
    const r = emiSchedule(P, 8.5, 240, "2026-10", { oneTime: [{ month: "2027-09", amount: 500_000 }], effect: "emi" });
    expect(r.rows).toHaveLength(240);
    expect(r.finalEmi).toBeLessThan(base.emi);
    expect(r.rows[11].emi).toBeCloseTo(base.emi, 6); // month 12 (2027-09) still pays the old EMI
    expect(r.rows[12].emi).toBeCloseTo(r.finalEmi, 6);
    expect(r.totalInterest).toBeLessThan(base.totalInterest);
    expect(paidOff(r)).toBeCloseTo(P, 4);
  });

  it("reducing tenure saves more interest than reducing the EMI", () => {
    const pp = { oneTime: [{ month: "2027-09", amount: 500_000 }] };
    const tenure = emiSchedule(P, 8.5, 240, "2026-10", { ...pp, effect: "tenure" });
    const emi = emiSchedule(P, 8.5, 240, "2026-10", { ...pp, effect: "emi" });
    expect(tenure.totalInterest).toBeLessThan(emi.totalInterest);
  });

  it("pays a yearly lump sum in the chosen month and steps the EMI up each anniversary", () => {
    const r = emiSchedule(P, 8.5, 240, "2026-10", { yearly: 100_000, yearlyMonth: 3, stepUpPct: 5 });
    expect(r.rows.filter((x) => x.prepayment > 0).every((x) => x.month.endsWith("-03"))).toBe(true);
    expect(r.rows[12].emi).toBeCloseTo(base.emi * 1.05, 6);
    expect(paidOff(r)).toBeCloseTo(P, 4);
  });

  it("starts the recurring prepayments in the chosen month, and only those", () => {
    // A lock-in: nothing extra until Oct 2027, a year after the first EMI.
    const r = emiSchedule(P, 8.5, 240, "2026-10", {
      startMonth: "2027-10",
      monthly: 5000,
      yearly: 100_000,
      yearlyMonth: 3,
      stepUpPct: 5,
      oneTime: [{ month: "2027-01", amount: 50_000 }],
    });
    const before = r.rows.filter((x) => x.month < "2027-10");
    // Only the one-time payment, which has its own month, lands before the start.
    expect(before.filter((x) => x.prepayment > 0).map((x) => [x.month, x.prepayment])).toEqual([["2027-01", 50_000]]);
    expect(r.rows.find((x) => x.month === "2027-10")!.prepayment).toBeCloseTo(5000, 6);
    // Mar 2027 is before the start: no yearly lump; Mar 2028 has it on top of the monthly extra.
    expect(r.rows.find((x) => x.month === "2028-03")!.prepayment).toBeCloseTo(105_000, 6);
    // The first anniversary (Oct 2027) is on the start month, so the step-up begins there.
    expect(r.rows[11].emi).toBeCloseTo(base.emi, 6);
    expect(r.rows[12].emi).toBeCloseTo(base.emi * 1.05, 6);
    expect(paidOff(r)).toBeCloseTo(P, 4);
    // Starting later prepays less and saves less than starting at once.
    const atOnce = emiSchedule(P, 8.5, 240, "2026-10", { monthly: 5000, yearly: 100_000, yearlyMonth: 3, stepUpPct: 5, oneTime: [{ month: "2027-01", amount: 50_000 }] });
    expect(r.totalInterest).toBeGreaterThan(atOnce.totalInterest);
  });

  it("a step-up waits for the first anniversary on or after the start month", () => {
    const r = emiSchedule(P, 8.5, 240, "2026-10", { startMonth: "2027-12", stepUpPct: 10 });
    expect(r.rows[12].emi).toBeCloseTo(base.emi, 6); // Oct 2027: before the start
    expect(r.rows[24].emi).toBeCloseTo(base.emi * 1.1, 6); // Oct 2028: first anniversary after it
  });

  it("never prepays more than is owed", () => {
    const r = emiSchedule(100_000, 10, 12, "2026-10", { oneTime: [{ month: "2026-10", amount: 1_000_000 }] });
    expect(r.rows).toHaveLength(1);
    expect(r.totalPrepaid).toBeLessThan(100_000);
    expect(r.rows[0].balance).toBe(0);
  });

  it("labels durations", () => {
    expect(durationLabel(51)).toBe("4 yr 3 mo");
    expect(durationLabel(12)).toBe("1 yr");
  });
});
