import { describe, expect, it } from "vitest";

import type { CompareFund, CompareResult } from "./api/compare";
import { leader, performanceLeaderPool, periodExplanation, terLeaderPool } from "./compare";

function fund(code: number, over: Partial<CompareFund> = {}): CompareFund {
  return {
    scheme_code: code,
    display_name: `Fund ${code} (Direct - Growth) [${code}]`,
    status: "ok",
    is_active: true,
    ter_status: "official",
    expense_ratio: 0.5,
    common: { start_date: "2026-06-01", start_nav: 100, end_date: "2026-09-01", end_nav: 110, return_pct: 10, cagr_pct: null, days: 92 },
    ...over,
  };
}

function result(funds: CompareFund[], common: Partial<NonNullable<CompareResult["common"]>> = {}): CompareResult {
  return {
    requested: { start: "2026-06-01", end: "2026-09-24", days: 115 },
    common: { start: "2026-06-01", end: "2026-09-02", days: 93, coverage_pct: 80.87, limited_start_by: [], limited_end_by: [], fund_count: funds.length, ...common },
    rf_pct: 6.5,
    funds,
    correlation: null,
    limits: { max_funds: 8, anchor_max_gap_days: 10, stale_after_days: 30, short_sample_returns: 60, rolling_days: 365 },
  };
}

describe("leader cards", () => {
  it("never crowns an IDCW plan or a fund outside the common period on performance", () => {
    const idcw = fund(1, { is_idcw: true, common: { ...fund(1).common!, return_pct: 25 } });
    const stale = fund(2, { status: "stale", common: null });
    const growth = fund(3);
    const pool = performanceLeaderPool([idcw, stale, growth]);
    expect(pool.map((f) => f.scheme_code)).toEqual([3]);
    expect(leader(pool, (f) => f.common?.return_pct, "max")!.fund.scheme_code).toBe(3);
  });

  it("names the lowest TER only among schemes still publishing", () => {
    // A wound-up plan keeps its last disclosed TER (Quant Liquid [148510], last NAV Dec 2025).
    const woundUp = fund(1, { is_active: false, status: "stale", expense_ratio: 0.05 });
    const unknown = fund(2, { ter_status: "unknown", expense_ratio: null });
    const live = fund(3, { expense_ratio: 0.31 });
    const pick = leader(terLeaderPool([woundUp, unknown, live]), (f) => f.expense_ratio, "min");
    expect(pick!.fund.scheme_code).toBe(3);
  });
});

describe("period explanation", () => {
  it("says how far behind the fund that set the end is, and warns when it looks stopped", () => {
    // Nippon's Sep 2026 target-maturity fund last priced on 2 Sep; everyone else on 24 Sep.
    const lines = periodExplanation(result([fund(1), fund(2)], { limited_end_by: [2], latest_end: "2026-09-24" }));
    const line = lines.find((l) => l.text.includes("has no NAV after"))!;
    expect(line.text).toContain("22 days before the newest NAV");
    expect(line.level).toBe("warning");
    expect(line.text).toContain("stopped publishing");
  });

  it("keeps a one-day publication lag informational", () => {
    const lines = periodExplanation(result([fund(1), fund(2)], { end: "2026-09-23", limited_end_by: [2], latest_end: "2026-09-24" }));
    const line = lines.find((l) => l.text.includes("has no NAV after"))!;
    expect(line.text).toContain("1 day before the newest NAV");
    expect(line.level).toBe("info");
  });
});
