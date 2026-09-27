import { describe, expect, it } from "vitest";

import {
  effectiveWeights,
  equalWeights,
  fundColor,
  ordinal,
  parseWeightsParam,
  rollingReturns,
  serializeWeights,
  sipDayLabel,
  SIP_DAY_CHOICES,
  summarizeWeights,
  xirrNoteText,
} from "./backtest";

describe("Backtest weights", () => {
  it("round-trips the URL form and drops malformed pairs", () => {
    expect(parseWeightsParam("122639:60,118989:40")).toEqual({ 122639: 60, 118989: 40 });
    expect(parseWeightsParam("abc:1,5:,:7,9:x,3:2.5")).toEqual({ 3: 2.5 });
    expect(parseWeightsParam(null)).toEqual({});
  });

  it("serialises only the funds still selected", () => {
    expect(serializeWeights([1, 2], { 1: 70, 2: 30, 3: 99 })).toBe("1:70,2:30");
    expect(serializeWeights([], { 1: 70 })).toBeUndefined();
  });

  it("gives a newly added fund an equal share instead of a silent 0%", () => {
    expect(effectiveWeights([1, 2, 3], { 1: 50, 2: 50 })).toEqual({ 1: 50, 2: 50, 3: 33.33 });
    // An explicit 0 is the user's choice and stays 0.
    expect(effectiveWeights([1, 2], { 1: 100, 2: 0 })).toEqual({ 1: 100, 2: 0 });
  });

  it("equal weights split 100 across the selection", () => {
    expect(equalWeights([1, 2, 3, 4])).toEqual({ 1: 25, 2: 25, 3: 25, 4: 25 });
  });

  it("normalises to 100% and says when it had to", () => {
    const s = summarizeWeights([1, 2], { 1: 3, 2: 1 });
    expect(s.sum).toBe(4);
    expect(s.normalized[1]).toBeCloseTo(75);
    expect(s.rescaled).toBe(true);
    expect(summarizeWeights([1, 2], { 1: 60, 2: 40 }).rescaled).toBe(false);
    expect(summarizeWeights([1, 2], { 1: 120, 2: -20 }).hasNegative).toBe(true);
  });
});

describe("Rolling returns", () => {
  it("measures each window from the last point on or before its start", () => {
    const dates = ["2024-01-01", "2024-01-02", "2024-01-05", "2024-01-08"];
    const levels = [100, 101, 102, 110];
    const r = rollingReturns(dates, levels, 4);
    // 5 Jan: base is 1 Jan's level -> +2%; 8 Jan: base is 2 Jan's (last point on/before 4 Jan) -> 110/101-1.
    expect(r.x).toEqual(["2024-01-05", "2024-01-08"]);
    expect(r.y[0]).toBeCloseTo(2);
    expect(r.y[1]).toBeCloseTo((110 / 101 - 1) * 100);
  });

  it("annualises windows longer than a year", () => {
    const dates = ["2020-01-01", "2023-01-01"];
    const r = rollingReturns(dates, [100, 133.1], 3 * 365);
    const span = 1096; // 2020 is a leap year
    expect(r.y[0]).toBeCloseTo((Math.pow(1.331, 365.25 / span) - 1) * 100, 6);
  });

  it("returns nothing for a series shorter than the window", () => {
    expect(rollingReturns(["2024-01-01", "2024-02-01"], [100, 101], 365).x).toEqual([]);
  });
});

describe("Labels", () => {
  it("explains a missing XIRR", () => {
    expect(xirrNoteText("too_short")).toBe("Withheld: under 30 days");
    expect(xirrNoteText("no_solution")).toMatch(/No stable solution/);
  });

  it("names SIP dates", () => {
    expect(ordinal(1)).toBe("1st");
    expect(ordinal(12)).toBe("12th");
    expect(ordinal(22)).toBe("22nd");
    expect(ordinal(23)).toBe("23rd");
    expect(sipDayLabel("start")).toBe("Same day as the start date");
    expect(sipDayLabel(31)).toBe("Last day of the month");
    expect(sipDayLabel(5)).toBe("5th of every month");
    expect(sipDayLabel(30)).toBe("30th of every month (month end in shorter months)");
  });

  it("offers every SIP day the backend accepts (29 and 30 were missing)", () => {
    expect(SIP_DAY_CHOICES).toEqual(["start", ...Array.from({ length: 31 }, (_, i) => i + 1)]);
  });

  it("keeps a fund's colour by its slot", () => {
    expect(fundColor(0)).toBe("#2a78d6");
    expect(fundColor(8)).toBe(fundColor(0));
  });
});
