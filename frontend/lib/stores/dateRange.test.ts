import { describe, expect, it } from "vitest";

import { computeRangeForPreset, PRESET_OPTIONS } from "./dateRange";

const DB_MIN = "2008-10-02"; // real bound from the live database, for realism
const DB_MAX = "2026-09-10";

function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(b + "T00:00:00Z") - Date.parse(a + "T00:00:00Z")) / 86400000);
}

describe("computeRangeForPreset", () => {
  // Table-driven per the migration plan's explicit verification step: exercise
  // all 8 presets against real-shaped bounds. dbMin here is far enough in the
  // past that none of these presets get clamped by it -- that clamping case
  // is tested separately below.
  const relativePresets: [string, number][] = [
    ["Past 7 Days (1W)", 7],
    ["Past 30 Days (1M)", 30],
    ["Past 90 Days (3M)", 90],
    ["Past 180 Days (6M)", 180],
    ["Past 1 Year (12M)", 365],
  ];

  it.each(relativePresets)("'%s' ends at dbMax and spans exactly %i days when dbMin doesn't clamp it", (preset, expectedDays) => {
    const result = computeRangeForPreset(preset, DB_MIN, DB_MAX);
    expect(result).not.toBeNull();
    expect(result!.end).toBe(DB_MAX);
    expect(daysBetween(result!.start, DB_MAX)).toBe(expectedDays);
  });

  it("clamps to dbMin when the preset's window would start before the database's earliest data", () => {
    // dbMin is only 3 days before dbMax -- every preset longer than 3 days must clamp to dbMin,
    // not go negative or before the data actually starts.
    const tightDbMin = "2026-09-07";
    for (const [preset] of relativePresets) {
      const result = computeRangeForPreset(preset, tightDbMin, DB_MAX);
      expect(result!.start).toBe(tightDbMin);
      expect(result!.end).toBe(DB_MAX);
    }
  });

  it("'Since 2020' clamps to 2020-01-01 or dbMin, whichever is later", () => {
    expect(computeRangeForPreset("Since 2020", DB_MIN, DB_MAX)).toEqual({ start: "2020-01-01", end: DB_MAX });
    // A database that only goes back to 2022 should clamp to dbMin, not 2020.
    expect(computeRangeForPreset("Since 2020", "2022-01-01", DB_MAX)).toEqual({ start: "2022-01-01", end: DB_MAX });
  });

  it("'All Available' returns the full dbMin..dbMax span, unmodified", () => {
    expect(computeRangeForPreset("All Available", DB_MIN, DB_MAX)).toEqual({ start: DB_MIN, end: DB_MAX });
  });

  it("'Custom Range' (and any unrecognized preset) returns null -- the caller must not overwrite a manually-picked range", () => {
    expect(computeRangeForPreset("Custom Range", DB_MIN, DB_MAX)).toBeNull();
    expect(computeRangeForPreset("something-unexpected", DB_MIN, DB_MAX)).toBeNull();
  });

  it("every PRESET_OPTIONS entry is handled explicitly (no silent fallthrough to null for a real preset)", () => {
    for (const preset of PRESET_OPTIONS) {
      const result = computeRangeForPreset(preset, DB_MIN, DB_MAX);
      if (preset === "Custom Range") {
        expect(result).toBeNull();
      } else {
        expect(result).not.toBeNull();
      }
    }
  });

  it("windows nest correctly: a longer preset's start is never later than a shorter preset's start", () => {
    const starts = relativePresets.map(([preset]) => computeRangeForPreset(preset, DB_MIN, DB_MAX)!.start);
    const sorted = [...starts].sort();
    // relativePresets is already ordered shortest->longest, so starts should already be
    // descending (longer window = earlier start) -- i.e. reverse-sorted.
    expect(starts).toEqual([...sorted].reverse());
  });
});
