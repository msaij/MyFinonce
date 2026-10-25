import { describe, expect, it } from "vitest";

import { applyKey, applyKeyAt, asOperand, evaluate, filterTyped, formatExpression, formatIndianInteger, formatNumber, plainNumber, readPaste, sanitizePaste } from "./expression";

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
  it("implies × before a point after a bracket", () => {
    expect(press(["(", "3", ")", ".", "5"])).toBe("(3)×0.5");
    expect(val(press(["(", "3", ")", ".", "5"]))).toBe(1.5);
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
  it("± works on a number shown with grouping commas", () => {
    expect(applyKey("1,000", "±", false)).toBe("−1,000");
    expect(applyKey("5+(−3,000)", "±", false)).toBe("5+3,000");
    expect(applyKey("2×1,000", "±", false)).toBe("2×−1,000");
  });
  it("x² and √ build valid expressions", () => {
    expect(press(["9", "x²"])).toBe("9^2");
    expect(press(["2", "√", "9"])).toBe("2×√9");
  });
});

describe("applyKeyAt (a key with the caret mid-expression)", () => {
  it("follows the same rules as typing at the end", () => {
    expect(applyKeyAt("5+3", 2, 2, "×")).toEqual({ text: "5×3", caret: 2 });
    expect(applyKeyAt("12+3", 2, 2, "(")?.text).toBe("12×(+3");
    expect(applyKeyAt("1,000", 1, 1, "5")).toEqual({ text: "15,000", caret: 2 });
  });
  it("ignores ± and a second decimal point in the number at the caret", () => {
    expect(applyKeyAt("1.5", 1, 1, ".")).toBeNull();
    expect(applyKeyAt("5+3", 2, 2, "±")).toBeNull();
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
import { addMonths, durationLabel, emiSchedule, groupByYear, monthlyEmi, monthsToYears, yearsToMonths } from "./emi";

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
    expect(val("12345.67−12345.60")).toBe(0.07);
    expect(val("99999.99−99999")).toBe(0.99);
    expect(val("10250.30−10000")).toBe(250.3);
  });
  it("keeps every digit typed, and a result reads back as itself", () => {
    expect(val("1234567890123")).toBe(1234567890123);
    expect(val("10000000000.25")).toBe(10000000000.25);
    for (const v of [val("1÷3"), val("√2"), val("1234567890123")]) expect(val(asOperand(v))).toBe(v);
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
  it("switching the tenure between months and years keeps the same loan", () => {
    expect(monthsToYears(15)).toBe(1.25);
    for (let n = 1; n <= 600; n++) expect(yearsToMonths(monthsToYears(n))).toBe(n);
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

describe("EMI golden cases (worked by hand)", () => {
  it("a prepayment lands after that month's interest and EMI", () => {
    // 12,000 at 12% (1% a month) over 3 months; 1,000 prepaid in month 1.
    // EMI = 120 × 1.01³ / (1.01³ − 1) = 4080.2653. Month 1: interest 120, balance after
    // EMI and prepayment 7039.7347. Month 2: interest 70.3973, balance 3029.8667.
    // Month 3: interest 30.2987, and the last payment settles 3029.8667 + 30.2987.
    const r = emiSchedule(12_000, 12, 3, "2026-10", { oneTime: [{ month: "2026-10", amount: 1000 }] });
    expect(r.emi).toBeCloseTo(4080.2653, 4);
    expect(r.rows[0].interest).toBeCloseTo(120, 9);
    expect(r.rows).toHaveLength(3);
    expect(r.rows[2].emi).toBeCloseTo(3060.1653, 4);
    expect(r.totalInterest).toBeCloseTo(220.696, 3);
  });
  it("a step-up compounds each anniversary", () => {
    const base = emiSchedule(2_500_000, 8.5, 240, "2026-10");
    const r = emiSchedule(2_500_000, 8.5, 240, "2026-10", { stepUpPct: 5 });
    expect(r.rows[24].emi).toBeCloseTo(base.emi * 1.05 ** 2, 6); // not 1.10, as a linear step would give
  });
  it("at 0%, the loan is split evenly with no interest", () => {
    const r = emiSchedule(120_000, 0, 12, "2026-10");
    expect(r.rows).toHaveLength(12);
    for (const row of r.rows) expect(row.emi).toBeCloseTo(10_000, 9);
    expect(r.totalInterest).toBe(0);
  });
  it("a one-month loan pays the principal plus a month's interest", () => {
    const r = emiSchedule(1000, 12, 1, "2026-10");
    expect(r.rows).toHaveLength(1);
    expect(r.rows[0].emi).toBeCloseTo(1010, 9);
    expect(r.rows[0].balance).toBe(0);
  });
});

describe("formatIndianInteger & formatExpression", () => {
  it("formats integer strings with Indian digit grouping", () => {
    expect(formatIndianInteger("0")).toBe("0");
    expect(formatIndianInteger("50")).toBe("50");
    expect(formatIndianInteger("500")).toBe("500");
    expect(formatIndianInteger("1000")).toBe("1,000");
    expect(formatIndianInteger("10000")).toBe("10,000");
    expect(formatIndianInteger("100000")).toBe("1,00,000");
    expect(formatIndianInteger("1234567")).toBe("12,34,567");
    expect(formatIndianInteger("12345678")).toBe("1,23,45,678");
  });

  it("formats numbers inside expressions with Indian commas as digits are entered", () => {
    expect(formatExpression("123213123")).toBe("12,32,13,123");
    expect(formatExpression("1,2,3,2,1,3,123")).toBe("12,32,13,123");
    expect(formatExpression("1,2000×12")).toBe("12,000×12");
    expect(formatExpression("1,200×12")).toBe("1,200×12");
    expect(formatExpression("1000")).toBe("1,000");
    expect(formatExpression("100000")).toBe("1,00,000");
    expect(formatExpression("1000.")).toBe("1,000.");
    expect(formatExpression("1000.5")).toBe("1,000.5");
    expect(formatExpression("1000.050")).toBe("1,000.050");
    expect(formatExpression("100000+50000")).toBe("1,00,000+50,000");
    expect(formatExpression("5×(−3000)")).toBe("5×(−3,000)");
    expect(formatExpression("√(1440000)")).toBe("√(14,40,000)");
    expect(formatExpression("2^10")).toBe("2^10");
    expect(formatExpression("200+10%")).toBe("200+10%");
  });
});

describe("sanitizePaste", () => {
  it("cleans currency symbols, labels, and grouping commas from pasted strings", () => {
    expect(sanitizePaste("12,34,567")).toBe("1234567");
    expect(sanitizePaste("₹ 1,50,000.50")).toBe("150000.50");
    expect(sanitizePaste("Rs. 50,000 + 18%")).toBe("50000+18%");
    expect(sanitizePaste("1000 * 25 / 4")).toBe("1000×25÷4");
    expect(sanitizePaste("100 + 200 = 300")).toBe("100+200");
    expect(sanitizePaste("  50000 =  ")).toBe("50000");
    expect(sanitizePaste("$2,500.00")).toBe("2500.00");
    expect(sanitizePaste("(10,000 + 2,000) * 15%")).toBe("(10000+2000)×15%");
  });
});

describe("readPaste", () => {
  it("refuses text it would have to guess at, instead of turning it into another number", () => {
    for (const raw of ["₹2.5 Cr", "50k", "1e6", "100\n200", "1e-7"]) expect(readPaste(raw).ok).toBe(false);
    expect(readPaste("100\n200")).toEqual({ ok: false, error: "More than one number" });
    expect(readPaste("50k")).toEqual({ ok: false, error: "Couldn't read a number" });
    expect(readPaste("₹ ")).toEqual({ ok: false, error: "Nothing to paste" });
    expect(readPaste("1".repeat(501)).ok).toBe(false);
  });
  it("accepts everything sanitizePaste cleans", () => {
    for (const raw of ["12,34,567", "₹ 1,50,000.50", "Rs. 50,000 + 18%", "1000 * 25 / 4", "100 + 200 = 300", "  50000 =  ", "$2,500.00", "(10,000 + 2,000) * 15%"]) {
      expect(readPaste(raw)).toEqual({ ok: true, text: sanitizePaste(raw) });
    }
  });
  it("a copied result pastes back as the same number", () => {
    expect(plainNumber(-1e-7)).toBe("-0.0000001");
    for (const v of [1e-7, -1e-7, 1e21, -3]) expect(val(sanitizePaste(plainNumber(v)))).toBe(v);
  });
});

describe("asOperand", () => {
  it("writes numbers without exponents, bracketing negatives", () => {
    expect(asOperand(1e-7)).toBe("0.0000001");
    expect(asOperand(1.5e-7)).toBe("0.00000015");
    expect(asOperand(1e21)).toBe("1000000000000000000000");
    expect(asOperand(-1e-7)).toBe("(−0.0000001)");
    expect(asOperand(-1e21)).toBe("(−1000000000000000000000)");
    expect(asOperand(12.5)).toBe("12.5");
    expect(asOperand(-3)).toBe("(−3)");
    expect(evaluate(asOperand(1e-7) + "×2")).toEqual({ ok: true, value: 2e-7 });
  });
});

describe("filterTyped", () => {
  it("keeps only calculator characters, stripping letters and currency symbols", () => {
    expect(filterTyped("₹1,23,456 abc")).toBe("1,23,456");
    expect(filterTyped("$5 * 3 / 2 - 1")).toBe("5×3÷2−1");
    expect(filterTyped("(2+3)^2%√9")).toBe("(2+3)^2%√9");
    // After regrouping, typed text matches what a paste of the same text gives.
    expect(formatExpression(filterTyped("₹1,23,456"))).toBe(formatExpression(sanitizePaste("₹1,23,456")));
  });
});

