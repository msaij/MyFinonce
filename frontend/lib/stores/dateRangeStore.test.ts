import { beforeEach, describe, expect, it } from "vitest";

import { isIsoDate, useDateRangeStore } from "./dateRange";

/**
 * Tests the store's live-edge-slide rule directly -- the single most
 * important behavior named in the migration plan: "mock /api/meta/status's
 * data_version/max_date moving forward, confirm a 'Past 90 Days' window
 * slides automatically."
 */
describe("useDateRangeStore live-edge-slide", () => {
  beforeEach(() => {
    localStorage.clear();
    useDateRangeStore.setState({
      preset: "Past 90 Days (3M)",
      start: "",
      end: "",
      dbMinDate: null,
      dbMaxDate: null,
      isPlaceholder: false,
    });
  });

  it("seeds the window from the current preset on first sync", () => {
    useDateRangeStore.getState().syncBounds("2020-01-01", "2026-09-10");
    const state = useDateRangeStore.getState();
    expect(state.end).toBe("2026-09-10");
    expect(state.dbMaxDate).toBe("2026-09-10");
  });

  it("slides the window forward when new data lands while tracking the live edge", () => {
    const store = useDateRangeStore.getState();
    store.syncBounds("2020-01-01", "2026-09-10");
    const firstEnd = useDateRangeStore.getState().end;
    expect(firstEnd).toBe("2026-09-10"); // was tracking the live edge

    // New data lands: db_max advances by one day, as if a sync just landed.
    useDateRangeStore.getState().syncBounds("2020-01-01", "2026-09-11");
    const after = useDateRangeStore.getState();
    expect(after.end).toBe("2026-09-11"); // slid forward automatically
    expect(after.start > "2026-09-10").toBe(false); // start also moved forward correspondingly
  });

  it("does NOT slide when the user has picked a Custom Range", () => {
    useDateRangeStore.getState().syncBounds("2020-01-01", "2026-09-10");
    useDateRangeStore.getState().setCustomRange("2025-01-01", "2025-06-01");
    expect(useDateRangeStore.getState().preset).toBe("Custom Range");

    useDateRangeStore.getState().syncBounds("2020-01-01", "2026-09-11");
    const after = useDateRangeStore.getState();
    // A custom range must stay exactly as the user set it, even though new data landed.
    expect(after.start).toBe("2025-01-01");
    expect(after.end).toBe("2025-06-01");
  });

  it("does NOT slide when the window was NOT tracking the live edge (user scrolled back historically on a preset, then new data landed)", () => {
    useDateRangeStore.getState().syncBounds("2020-01-01", "2026-09-10");
    // Simulate the window's end no longer matching the live edge (e.g. some other
    // interaction moved it) without changing the preset away from a relative one.
    useDateRangeStore.setState({ end: "2026-08-01" });

    useDateRangeStore.getState().syncBounds("2020-01-01", "2026-09-11");
    expect(useDateRangeStore.getState().end).toBe("2026-08-01"); // unchanged -- wasn't tracking live edge
  });

  it("forces a fresh reseed when transitioning from the empty-database placeholder to a real sync, even if the real max_date coincidentally equals the placeholder", () => {
    // Regression test: found live when a real first sync happened to land on the exact
    // same date DateRangePicker's empty-DB fallback had already seeded (today's date is
    // a very plausible real max_date right after a fresh sync) -- plain dbMax
    // value-equality alone couldn't tell "nothing changed" from "the placeholder just
    // happened to match", so the window stayed stuck at a degenerate 0-day range
    // (start === end === today) instead of expanding to a real 90-day preset window.
    const today = "2026-09-11";
    useDateRangeStore.getState().syncBounds(today, today, true); // DateRangePicker's placeholder fallback
    const placeholderState = useDateRangeStore.getState();
    expect(placeholderState.start).toBe(today);
    expect(placeholderState.end).toBe(today); // degenerate 0-day window, as expected for a placeholder

    // A real sync lands, with max_date coincidentally equal to the placeholder.
    useDateRangeStore.getState().syncBounds("2008-10-02", today, false);
    const after = useDateRangeStore.getState();
    expect(after.end).toBe(today);
    expect(after.start).toBe("2026-06-13"); // properly recomputed: 90 real days back from today
    expect(after.isPlaceholder).toBe(false);
  });

  it("does NOT force a reseed on every call once real data is flowing (isPlaceholder stays false)", () => {
    useDateRangeStore.getState().syncBounds("2008-10-02", "2026-09-10", false);
    useDateRangeStore.setState({ end: "2026-08-01" }); // simulate "not tracking the live edge" again

    useDateRangeStore.getState().syncBounds("2008-10-02", "2026-09-11", false);
    expect(useDateRangeStore.getState().end).toBe("2026-08-01"); // still respects the normal slide rule
  });

  it("preserves custom range set before first syncBounds (e.g. from bookmarked URL)", () => {
    useDateRangeStore.getState().setCustomRange("2024-03-01", "2024-09-01");
    useDateRangeStore.getState().syncBounds("2020-01-01", "2026-09-10");
    const state = useDateRangeStore.getState();
    expect(state.preset).toBe("Custom Range");
    expect(state.start).toBe("2024-03-01");
    expect(state.end).toBe("2024-09-01");
    expect(state.dbMinDate).toBe("2020-01-01");
    expect(state.dbMaxDate).toBe("2026-09-10");
  });
});

describe("isIsoDate", () => {
  it("accepts a real day and rejects one that only has the shape", () => {
    expect(isIsoDate("2026-02-28")).toBe(true);
    expect(isIsoDate("2024-02-29")).toBe(true);
    // V8 rolls this over to 3 March; Firefox calls it Invalid Date. Neither is the day asked for.
    expect(isIsoDate("2026-02-31")).toBe(false);
    expect(isIsoDate("2026-13-01")).toBe(false);
    expect(isIsoDate("2026-1-01")).toBe(false);
    expect(isIsoDate("")).toBe(false);
    expect(isIsoDate(null)).toBe(false);
  });
});

describe("a stored window that is not a real day", () => {
  beforeEach(() => {
    localStorage.clear();
    useDateRangeStore.setState({ preset: "Custom Range", start: "", end: "", dbMinDate: null, dbMaxDate: null, isPlaceholder: false });
  });

  it("is replaced on the next sync instead of being sent with every request", () => {
    // What ?start=2026-02-31 used to leave in localStorage, surviving the URL's removal.
    useDateRangeStore.setState({ start: "2026-02-31", end: "2026-03-15", dbMinDate: "2020-01-01", dbMaxDate: "2026-09-10" });
    useDateRangeStore.getState().syncBounds("2020-01-01", "2026-09-10");
    const after = useDateRangeStore.getState();
    expect(after.start).toBe("2020-01-01");
    expect(after.end).toBe("2026-09-10");
  });
});
