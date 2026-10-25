import { describe, expect, it } from "vitest";

import {
  LAGGARD_LABELS,
  QUADRANT_LABELS,
  ROTATION_LABELS,
  VOL_LOOKBACKS,
  ambiguousCategories,
  formatCrore,
  formatPp,
  onePerFund,
  fundNameKey,
  peerGroupLabel,
  periodLabel,
  pickOption,
  robustRange,
  windowDays,
} from "./overview";

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

  it("matches twins whose names carry the plan, without merging different funds", () => {
    const rows = [
      { scheme_name: "Alpha Fund - Regular Plan - Growth", option_type: "Growth", plan_type: "Regular", id: 1 },
      { scheme_name: "Alpha Fund - Direct Plan - Growth", option_type: "Growth", plan_type: "Direct", id: 2 },
      { scheme_name: "Gamma Regular Savings Fund", option_type: "Growth", plan_type: "Direct", id: 3 },
      { scheme_name: "Gamma Savings Fund", option_type: "Growth", plan_type: "Direct", id: 4 },
    ];
    expect(onePerFund(rows).map((r) => r.id)).toEqual([2, 3, 4]);
    expect(fundNameKey("Alpha Fund - Direct - Growth")).toBe("alpha fund - growth");
  });
});

describe("URL parameters", () => {
  const HORIZONS = ["1 Day", "1 Month (30D)"] as const;

  it("keeps a listed value and falls back for anything else, including Object.prototype's keys", () => {
    expect(pickOption("1 Day", HORIZONS, "1 Month (30D)")).toBe("1 Day");
    expect(pickOption("constructor", HORIZONS, "1 Month (30D)")).toBe("1 Month (30D)");
    expect(pickOption("__proto__", HORIZONS, "1 Month (30D)")).toBe("1 Month (30D)");
    expect(pickOption("toString", HORIZONS, "1 Month (30D)")).toBe("1 Month (30D)");
    expect(pickOption(null, HORIZONS, "1 Month (30D)")).toBe("1 Month (30D)");
    expect(pickOption(undefined, HORIZONS, "1 Month (30D)")).toBe("1 Month (30D)");
  });

  it("offers only the lookbacks the backend accepts (it answers 422 to any other)", () => {
    expect([...VOL_LOOKBACKS]).toEqual(["1Y", "6M", "3Y", "Full Window"]);
    expect(pickOption("2Y", VOL_LOOKBACKS, "1Y")).toBe("1Y");
  });
});

describe("Labels the tiles count by", () => {
  // The same literals are pinned in backend/tests/test_leaders_service.py: the tiles match
  // rows by exact text, so a label reworded on one side reads "0 funds" everywhere.
  it("match the backend's quadrant, laggard and rotation labels exactly", () => {
    expect(QUADRANT_LABELS).toEqual({
      aheadCalmer: "Ahead of peers, calmer",
      aheadBumpier: "Ahead of peers, bumpier",
      behindCalmer: "Behind peers, calmer",
      behindBumpier: "Behind peers, bumpier",
    });
    expect(LAGGARD_LABELS).toEqual({
      bottomDecile: "Bottom 10% of its peers",
      bottomQuarter: "Bottom quarter of its peers",
      withCategory: "Falling with its category",
      slightlyBehind: "Slightly behind its peers",
      notALaggard: "Not a laggard",
    });
    expect(ROTATION_LABELS).toEqual({ leading: "Leading", weakening: "Weakening", lagging: "Lagging", improving: "Improving" });
  });
});
