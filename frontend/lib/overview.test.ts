import { describe, expect, it } from "vitest";

import { ambiguousCategories, formatCrore, formatPp, onePerFund, peerGroupLabel, periodLabel, robustRange, windowDays } from "./overview";

describe("Chart helpers", () => {
  it("frames the bulk of the points and counts the outliers it leaves out", () => {
    const values = [...Array.from({ length: 200 }, (_, i) => (i - 100) / 50), 25];
    const r = robustRange(values)!;
    expect(r.range[0]).toBeLessThan(-2);
    expect(r.range[1]).toBeLessThan(5);
    expect(r.outside).toBe(1);
  });

  it("always keeps zero, the peer median, in view", () => {
    const r = robustRange([3, 4, 5, 6])!;
    expect(r.range[0]).toBeLessThan(0);
    expect(robustRange([])).toBeNull();
  });

  it("labels weeks by their start day and months by name", () => {
    expect(periodLabel("2026-06-29", "week")).toBe("29 Jun");
    expect(periodLabel("2026-07-01", "month")).toBe("Jul 2026");
  });
});

describe("Overview formatting", () => {
  it("states a gap between returns in percentage points, signed", () => {
    expect(formatPp(24.4877)).toBe("+24.49 pp");
    expect(formatPp(-0.354)).toBe("−0.35 pp");
    expect(formatPp(0.001)).toBe("0.00 pp");
    expect(formatPp(null)).toBe("-");
  });

  it("quotes assets in crore, and lakh crore at industry scale", () => {
    expect(formatCrore(8936975.67)).toBe("₹89.37 lakh cr");
    expect(formatCrore(1741.89)).toBe("₹1,742 cr");
    expect(formatCrore(null)).toBe("-");
  });

  it("counts the window in calendar days between the NAV dates used", () => {
    expect(windowDays("2026-06-26", "2026-09-24")).toBe(90);
    expect(windowDays(null, "2026-09-24")).toBeNull();
  });
});

describe("Peer groups", () => {
  it("names the asset class only where the category alone is ambiguous", () => {
    const amb = ambiguousCategories([
      { peer_category: "Index Funds", asset_class: "Equity" },
      { peer_category: "Index Funds", asset_class: "Debt" },
      { peer_category: "Liquid Fund", asset_class: "Cash & Liquid" },
    ]);
    expect(peerGroupLabel("Index Funds", "Debt", amb)).toBe("Index Funds (Debt)");
    expect(peerGroupLabel("Liquid Fund", "Cash & Liquid", amb)).toBe("Liquid Fund");
  });

  it("lists a fund once, keeping its Direct plan", () => {
    const rows = [
      { scheme_name: "Alpha Fund", option_type: "Growth", plan_type: "Regular", id: 1 },
      { scheme_name: "Alpha Fund", option_type: "Growth", plan_type: "Direct", id: 2 },
      { scheme_name: "Beta Fund", option_type: "Growth", plan_type: "Regular", id: 3 },
    ];
    expect(onePerFund(rows).map((r) => r.id)).toEqual([2, 3]);
  });
});
