import { describe, expect, it } from "vitest";

import type { CompareFund, CompareResult } from "./api/compare";
import {
  buildCompareFigure,
  correlationBg,
  correlationCellTitle,
  drawdownSeries,
  feeInr,
  fundColor,
  fundLine,
  leader,
  periodExplanation,
  rankText,
  ratioWithSample,
  sliceSeries,
  valueOf,
} from "./compare";

function fund(code: number, over: Partial<CompareFund> = {}): CompareFund {
  return {
    scheme_code: code,
    display_name: `Fund ${code} (Direct - Growth) [${code}]`,
    status: "ok",
    series: [
      ["2026-06-25", 100],
      ["2026-06-29", 110],
      ["2026-07-01", 99],
      ["2026-07-02", 121],
    ],
    common: { start_date: "2026-06-29", start_nav: 110, end_date: "2026-07-01", end_nav: 99, return_pct: -10, cagr_pct: null, days: 2 },
    ...over,
  };
}

function result(funds: CompareFund[], common: Partial<NonNullable<CompareResult["common"]>> | null = {}): CompareResult {
  return {
    requested: { start: "2026-06-26", end: "2026-07-02", days: 6 },
    common: common === null ? null : { start: "2026-06-26", end: "2026-07-01", days: 5, coverage_pct: 83.33, limited_start_by: [], limited_end_by: [], fund_count: funds.length, ...common },
    rf_pct: 6.5,
    funds,
    correlation: null,
    limits: { max_funds: 8, anchor_max_gap_days: 10, stale_after_days: 30, short_sample_returns: 60, rolling_days: 365 },
  };
}

describe("series shaping", () => {
  it("slices inclusively and drops nulls", () => {
    expect(sliceSeries([["2026-01-01", 1], ["2026-01-02", null], ["2026-01-03", 3]], "2026-01-01", "2026-01-03")).toEqual([
      ["2026-01-01", 1],
      ["2026-01-03", 3],
    ]);
  });

  it("measures drawdown from the running peak, not from the start", () => {
    expect(drawdownSeries([["a", 100], ["b", 120], ["c", 90], ["d", 130]]).map((p) => p[1])).toEqual([0, 0, -25, 0]);
  });

  it("starts every growth line at the invested amount on the fund's common-period start", () => {
    const line = fundLine(fund(1), "growth", 10000);
    expect(line[0]).toEqual(["2026-06-29", 10000]);
    expect(line[line.length - 1][1]).toBeCloseTo(9000, 6);
    // The 2 Jul NAV is outside the common period and must not be drawn.
    expect(line.map((p) => p[0])).not.toContain("2026-07-02");
  });

  it("draws nothing on the common-period charts for a fund left out of the period", () => {
    const stale = fund(2, { status: "stale", common: null });
    expect(fundLine(stale, "return", 10000)).toEqual([]);
    expect(fundLine(stale, "nav", 10000)).toHaveLength(4);
  });

  it("puts a Rs 10 fund and a Rs 1,000 fund on one scale in the own-window view", () => {
    const cheap = fund(1, { series: [["2026-06-26", 10], ["2026-06-29", 10.5], ["2026-06-30", 11]] });
    const dear = fund(2, { series: [["2026-06-26", 1000], ["2026-06-29", 1050], ["2026-06-30", 1100]] });
    for (const f of [cheap, dear]) {
      const line = fundLine(f, "nav", 10000);
      expect(line[0][1]).toBe(0);
      expect(line[2][1]).toBeCloseTo(10, 9);
    }
    const fig = buildCompareFigure([cheap, dear], "nav", 10000);
    const trace = fig.data[1] as { customdata: number[] };
    expect(trace.customdata).toEqual([1000, 1050, 1100]); // the published NAV, for the hover
    expect((fig.layout.yaxis as { ticksuffix?: string }).ticksuffix).toBe("%");
  });

  it("keeps a fund's colour by its position in the selection even when another fund has no line", () => {
    const fig = buildCompareFigure([fund(1, { common: null }), fund(2)], "return", 10000);
    expect(fig.data).toHaveLength(1);
    expect((fig.data[0] as { line: { color: string } }).line.color).toBe(fundColor(1));
    expect(fig.data[0].name).toBe("Fund 2 (Direct - Growth) [2]");
  });
});

describe("formatting", () => {
  it("prices fees and values off the invested amount", () => {
    expect(feeInr(10000, 0.1726)).toBeCloseTo(17.26, 6);
    expect(feeInr(10000, null)).toBeNull();
    expect(valueOf(10000, 1.8311)).toBeCloseTo(10183.11, 6);
  });

  it("states a rank only when both rank and group size exist", () => {
    expect(rankText(3, 44)).toBe("3 of 44");
    expect(rankText(null, 44)).toBe("-");
  });

  it("picks the leader and ignores funds without a value", () => {
    const fs = [fund(1), fund(2), fund(3)];
    const vals: Record<number, number | null> = { 1: 2, 2: null, 3: 5 };
    expect(leader(fs, (f) => vals[f.scheme_code], "max")!.fund.scheme_code).toBe(3);
    expect(leader(fs, (f) => vals[f.scheme_code], "min")!.fund.scheme_code).toBe(1);
    expect(leader([fund(2)], (f) => vals[f.scheme_code], "max")).toBeNull();
  });

  it("tints correlation cells by sign and strength", () => {
    expect(correlationBg(null)).toBe("transparent");
    expect(correlationBg(1)).toContain("42, 120, 214");
    expect(correlationBg(-0.5)).toContain("235, 104, 52");
  });
});

describe("period explanation", () => {
  it("names the fund that set the end date", () => {
    const lines = periodExplanation(result([fund(1), fund(2)], { limited_end_by: [2] }));
    // Worded "has no NAV after" (was "had not yet published"): the end-setter can also be a
    // fund that stopped publishing weeks ago, which "not yet" misdescribed.
    expect(lines.some((l) => l.text.includes("Fund 2 (Direct - Growth) [2] has no NAV after"))).toBe(true);
  });

  it("warns when a late starter shrinks the period, and names it with its first NAV date", () => {
    const late = fund(3, { own_window: { start_date: "2026-06-29", start_nav: 1, end_date: "2026-07-01", end_nav: 1, return_pct: 0, days: 2, anchored_before_start: false } });
    const lines = periodExplanation(result([fund(1), late], { limited_start_by: [3], coverage_pct: 40 }));
    expect(lines[0].level).toBe("warning");
    expect(lines.some((l) => l.text.includes("first NAV 29 Jun 2026"))).toBe(true);
  });

  it("flags IDCW plans, whose NAV return understates what a holder earned", () => {
    const lines = periodExplanation(result([fund(1), fund(2, { is_idcw: true })]));
    expect(lines.some((l) => l.text.includes("IDCW") && l.text.includes("understate"))).toBe(true);
  });

  it("explains an empty comparison instead of showing blank tables", () => {
    const lines = periodExplanation(result([fund(1, { status: "no_data", common: null })], null));
    expect(lines[0].level).toBe("warning");
  });
});

describe("risk-side helpers", () => {
  it("marks a ratio from a short sample with a dagger, and never a missing one", () => {
    expect(ratioWithSample(-2.78661, false)).toBe("-2.7866");
    expect(ratioWithSample(1.5, true)).toBe("1.5000 \u2020");
    expect(ratioWithSample(null, true)).toBe("-");
  });

  it("says what a correlation was measured on, and why one is missing", () => {
    expect(correlationCellTitle("A", "B", 0.83349, 156, 20, "weekly")).toBe("A vs B: 0.8335, over 156 common weekly returns.");
    expect(correlationCellTitle("A", "B", null, 13, 20, "daily")).toContain("only 13 daily returns");
    expect(correlationCellTitle("A", "B", null, 40, 20, "daily")).toContain("no variation");
  });

  it("measures drawdown from the running peak on the published NAVs", () => {
    const dd = drawdownSeries([["2026-01-01", 100], ["2026-01-02", 99], ["2026-01-05", 100], ["2026-01-06", 95]]);
    expect(dd.map((p) => Number(p[1].toFixed(4)))).toEqual([0, -1, 0, -5]);
  });
});
