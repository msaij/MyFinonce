import { describe, expect, it } from "vitest";

import { RISK_LEVELS, riskColor, riskRank } from "./riskometer";

describe("riskometer", () => {
  it("ranks the six levels by risk, not alphabetically", () => {
    const shuffled = ["Very High", "Low", "High", "Moderate", "Low to Moderate", "Moderately High"];
    expect([...shuffled].sort((a, b) => (riskRank(a) ?? 99) - (riskRank(b) ?? 99))).toEqual([...RISK_LEVELS]);
    expect(riskRank(" Moderate ")).toBe(3);
  });
  it("gives no rank or colour to a missing or unknown label", () => {
    expect(riskRank(null)).toBeNull();
    expect(riskRank("Extreme")).toBeNull();
    expect(riskColor(undefined)).toBeNull();
    expect(riskColor("Low")).toBe("#fdebd9");
  });
});
