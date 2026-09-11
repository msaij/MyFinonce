/**
 * Global active-date-window store. Ports fetcher/date_picker.py's
 * _range_for_preset()/init_date_state() logic -- see the migration plan's
 * "global date-window state" section for the full rationale.
 *
 * The one must-preserve business rule: if the active window is a relative
 * preset (not "Custom Range") AND its end was tracking the live edge (i.e.
 * equal to the previous known db max date), and new data lands (db max
 * advances), the window silently slides forward so "Past 90 Days" always
 * means 90 days ending at the latest sync -- not a frozen range from first
 * load. Manually picking a custom range opts out (sets preset to
 * "Custom Range", which computeRangeForPreset returns null for, so the
 * slide never fires).
 *
 * Collapses the original's canonical-vs-widget-mirror split
 * (active_start/active_end vs cal_range_widget/time_preset_widget) into one
 * slot -- that split was purely a Streamlit widget-key artifact, not a real
 * requirement.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export const PRESET_OPTIONS = [
  "Past 90 Days (3M)",
  "Past 30 Days (1M)",
  "Past 7 Days (1W)",
  "Past 180 Days (6M)",
  "Past 1 Year (12M)",
  "Since 2020",
  "All Available",
  "Custom Range",
] as const;

export type Preset = (typeof PRESET_OPTIONS)[number];

/** Pure port of date_picker.py's _range_for_preset(). Dates are ISO strings
 * (YYYY-MM-DD) throughout the store to keep persisted JSON simple and
 * timezone-unambiguous -- convert to Date only where day-math is needed. */
export function computeRangeForPreset(
  preset: string,
  dbMin: string,
  dbMax: string
): { start: string; end: string } | null {
  const toDate = (s: string) => new Date(s + "T00:00:00Z");
  const toIso = (d: Date) => d.toISOString().slice(0, 10);
  const maxIso = (a: string, b: string) => (a > b ? a : b);
  const dbMaxDate = toDate(dbMax);
  const minusDays = (n: number) => toIso(new Date(dbMaxDate.getTime() - n * 86400000));

  switch (preset) {
    case "Past 7 Days (1W)":
      return { start: maxIso(dbMin, minusDays(7)), end: dbMax };
    case "Past 30 Days (1M)":
      return { start: maxIso(dbMin, minusDays(30)), end: dbMax };
    case "Past 90 Days (3M)":
      return { start: maxIso(dbMin, minusDays(90)), end: dbMax };
    case "Past 180 Days (6M)":
      return { start: maxIso(dbMin, minusDays(180)), end: dbMax };
    case "Past 1 Year (12M)":
      return { start: maxIso(dbMin, minusDays(365)), end: dbMax };
    case "Since 2020":
      return { start: maxIso(dbMin, "2020-01-01"), end: dbMax };
    case "All Available":
      return { start: dbMin, end: dbMax };
    default:
      return null; // "Custom Range"
  }
}

interface DateRangeState {
  preset: Preset;
  start: string;
  end: string;
  dbMinDate: string | null;
  dbMaxDate: string | null;
  setPreset: (preset: Preset) => void;
  setCustomRange: (start: string, end: string) => void;
  /** Call whenever /api/meta/status is polled -- applies the live-edge-slide
   * rule if new data has landed and the window was tracking the live edge. */
  syncBounds: (dbMin: string, dbMax: string) => void;
}

const DEFAULT_PRESET: Preset = "Past 90 Days (3M)";

export const useDateRangeStore = create<DateRangeState>()(
  persist(
    (set, get) => ({
      preset: DEFAULT_PRESET,
      start: "",
      end: "",
      dbMinDate: null,
      dbMaxDate: null,

      setPreset: (preset) => {
        const { dbMinDate, dbMaxDate } = get();
        if (!dbMinDate || !dbMaxDate) {
          set({ preset });
          return;
        }
        const recomputed = computeRangeForPreset(preset, dbMinDate, dbMaxDate);
        set({
          preset,
          ...(recomputed ? { start: recomputed.start, end: recomputed.end } : {}),
        });
      },

      setCustomRange: (start, end) => {
        set({ preset: "Custom Range", start, end });
      },

      syncBounds: (dbMin, dbMax) => {
        const state = get();
        const prevDbMax = state.dbMaxDate;
        const boundsMissing = !state.dbMinDate || !state.dbMaxDate;

        if (boundsMissing) {
          // First load: seed the window from the current preset against real bounds.
          const recomputed = computeRangeForPreset(state.preset, dbMin, dbMax);
          set({
            dbMinDate: dbMin,
            dbMaxDate: dbMax,
            ...(recomputed ? { start: recomputed.start, end: recomputed.end } : { start: dbMin, end: dbMax }),
          });
          return;
        }

        const wasTrackingLive = prevDbMax !== null && state.end === prevDbMax;
        const dataAdvanced = dbMax !== prevDbMax;

        if (state.preset !== "Custom Range" && wasTrackingLive && dataAdvanced) {
          const recomputed = computeRangeForPreset(state.preset, dbMin, dbMax);
          set({
            dbMinDate: dbMin,
            dbMaxDate: dbMax,
            ...(recomputed ? { start: recomputed.start, end: recomputed.end } : {}),
          });
        } else {
          set({ dbMinDate: dbMin, dbMaxDate: dbMax });
        }
      },
    }),
    { name: "mf-date-range" }
  )
);
