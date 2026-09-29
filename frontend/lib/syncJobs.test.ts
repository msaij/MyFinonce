import { describe, expect, it } from "vitest";

import { durationText, nextScheduledRun, refreshSteps, triggerText, untilText } from "./syncJobs";

// 2026-09-29 15:00 IST = 09:30 UTC.
const IST_1500 = Date.UTC(2026, 8, 29, 9, 30);
const ist = (y: number, mo: number, d: number, h: number, mi: number) => Date.UTC(y, mo - 1, d, h, mi) / 1000 - 330 * 60;

describe("sync job wording", () => {
  it("names each trigger the way a person would", () => {
    expect(triggerText("scheduled_00:05")).toBe("nightly, 00:05");
    expect(triggerText("heartbeat")).toBe("hourly catch-up");
    expect(triggerText("manual")).toBe("started by you");
    expect(triggerText("something_new")).toBe("something_new");
    expect(triggerText(null)).toBe("");
  });
  it("gives durations in minutes and seconds", () => {
    expect(durationText(218.4)).toBe("3 min 38 s");
    expect(durationText(45)).toBe("45 s");
    expect(durationText(null)).toBe("");
  });
});

describe("next scheduled run, in IST", () => {
  it("is the evening NAV sync during the day", () => {
    const next = nextScheduledRun(IST_1500);
    expect(next.kind).toBe("nav");
    expect(next.at).toBe(ist(2026, 9, 29, 23, 30));
    expect(untilText(next.at, IST_1500)).toBe("in 8 h 30 min");
  });
  it("is the next night's full refresh once the evening sync has passed", () => {
    const next = nextScheduledRun(ist(2026, 9, 29, 23, 45) * 1000);
    expect(next.kind).toBe("full");
    expect(next.at).toBe(ist(2026, 9, 30, 0, 5));
  });
  it("is the full refresh just after midnight IST", () => {
    const next = nextScheduledRun(ist(2026, 9, 30, 0, 1) * 1000);
    expect(next.kind).toBe("full");
    expect(next.at).toBe(ist(2026, 9, 30, 0, 5));
  });
});

describe("full refresh steps", () => {
  it("shows a failed enrichment even when the run as a whole succeeded", () => {
    const steps = refreshSteps({
      ok: true,
      nav: { ok: true, message: "Synced 14,406 NAV records" },
      resolve: { ok: false, reason: "AMFI fund-performance feed returned no rows." },
      audit: { ok: true, summary: { errors: 1, error_rows: 160, warnings: 3 } },
    });
    expect(steps.map((s) => [s.name, s.ok])).toEqual([
      ["NAV download", true],
      ["Plan/option, riskometer & AUM", false],
      ["Data audit", false],
    ]);
    expect(steps[1].text).toBe("AMFI fund-performance feed returned no rows.");
    expect(steps[2].text).toContain("160 rows");
  });
  it("is empty without a result", () => {
    expect(refreshSteps(null)).toEqual([]);
  });
});
