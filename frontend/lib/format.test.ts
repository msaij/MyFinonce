import { describe, expect, it } from "vitest";

import { formatSignedPct, toneOf, toneOfShown } from "./format";

describe("toneOfShown", () => {
  it("colours a value by what is printed, so a rounded zero is never red or green", () => {
    expect(formatSignedPct(-0.004, 2)).toBe("0.00%");
    expect(toneOf(-0.004)).toBe("neg");
    expect(toneOfShown(-0.004)).toBe("neutral");
    expect(toneOfShown(0.004)).toBe("neutral");
    expect(toneOfShown(-0.006)).toBe("neg");
    expect(toneOfShown(0.006)).toBe("pos");
    expect(toneOfShown(0.04, 1)).toBe("neutral");
  });

  it("is neutral for a missing value", () => {
    expect(toneOfShown(null)).toBe("neutral");
    expect(toneOfShown(undefined)).toBe("neutral");
    expect(toneOfShown(Number.NaN)).toBe("neutral");
  });
});
