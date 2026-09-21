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
  "Past 3 Years (3Y)",
  "Past 5 Years (5Y)",
  "Past 10 Years (10Y)",
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
    case "Past 3 Years (3Y)":
      return { start: maxIso(dbMin, minusDays(3 * 365)), end: dbMax };
    case "Past 5 Years (5Y)":
      return { start: maxIso(dbMin, minusDays(5 * 365)), end: dbMax };
    case "Past 10 Years (10Y)":
      return { start: maxIso(dbMin, minusDays(10 * 365)), end: dbMax };
    case "Since 2020":
      return { start: maxIso(dbMin, "2020-01-01"), end: dbMax };
    case "All Available":
      return { start: dbMin, end: dbMax };
    default:
      return null; // "Custom Range"
  }
}

export const PLAN_TYPE_OPTIONS = ["All Plans", "Direct", "Regular"] as const;
export type PlanType = (typeof PLAN_TYPE_OPTIONS)[number];

export const OPTION_TYPE_OPTIONS = ["All Options", "Growth", "IDCW"] as const;
export type OptionType = (typeof OPTION_TYPE_OPTIONS)[number];

interface DateRangeState {
  preset: Preset;
  start: string;
  end: string;
  planType: PlanType;
  optionType: OptionType;
  dbMinDate: string | null;
  dbMaxDate: string | null;
  /** True when dbMinDate/dbMaxDate came from DateRangePicker's empty-database
   * fallback (today's date standing in for a real bound), not a real sync.
   * See syncBounds() below for why this needs tracking explicitly. */
  isPlaceholder: boolean;
  setPreset: (preset: Preset) => void;
  setCustomRange: (start: string, end: string) => void;
  setPlanType: (plan: PlanType) => void;
  setOptionType: (option: OptionType) => void;
  /** Call whenever /api/meta/status is polled -- applies the live-edge-slide
   * rule if new data has landed and the window was tracking the live edge.
   * `isPlaceholder` (default false) marks a call seeded from the empty-
   * database fallback rather than a real sync -- see the fallback's own
   * comment in DateRangePicker.tsx for why plain value-equality isn't
   * enough to detect "placeholder -> real" on its own (today's date is a
   * perfectly plausible real max_date too, especially right after a fresh
   * sync -- so a coincidental match must NOT be treated as "no change"). */
  syncBounds: (dbMin: string, dbMax: string, isPlaceholder?: boolean) => void;
}

const DEFAULT_PRESET: Preset = "Past 90 Days (3M)";

export const useDateRangeStore = create<DateRangeState>()(
  persist(
    (set, get) => ({
      preset: DEFAULT_PRESET,
      start: "",
      end: "",
      planType: "All Plans",
      optionType: "All Options",
      dbMinDate: null,
      dbMaxDate: null,
      isPlaceholder: false,

      setPlanType: (planType) => set({ planType }),
      setOptionType: (optionType) => set({ optionType }),

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
        set({ preset: "Custom Range", start, end, isPlaceholder: false });
      },

      syncBounds: (dbMin, dbMax, isPlaceholder = false) => {
        const state = get();
        const prevDbMax = state.dbMaxDate;
        // Missing bounds OR transitioning from a placeholder to a real sync both need the
        // same "seed fresh from the current preset" treatment -- a placeholder's dbMax
        // (today's date) can coincidentally equal a genuinely real max_date (very likely
        // right after a fresh sync), so plain value-equality can't tell "nothing changed"
        // from "the placeholder happened to match." The explicit flag can.
        const needsFreshSeed = !state.dbMinDate || !state.dbMaxDate || !state.start || !state.end || (state.isPlaceholder && !isPlaceholder);

        if (needsFreshSeed) {
          const recomputed = computeRangeForPreset(state.preset, dbMin, dbMax);
          const fallback = state.preset === "Custom Range" && state.start && state.end
            ? { start: state.start, end: state.end }
            : { start: dbMin, end: dbMax };
          set({
            dbMinDate: dbMin,
            dbMaxDate: dbMax,
            isPlaceholder,
            ...(recomputed ? { start: recomputed.start, end: recomputed.end } : fallback),
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
            isPlaceholder,
            ...(recomputed ? { start: recomputed.start, end: recomputed.end } : {}),
          });
        } else {
          set({ dbMinDate: dbMin, dbMaxDate: dbMax, isPlaceholder });
        }
      },
    }),
    {
      name: "mf-date-range",
      // See filters.ts's matching skipHydration comment -- same SSR/hydration-mismatch fix,
      // same reason (this store's start/end/preset/dbMinDate etc. are read directly during
      // render by every page via useDateRangeStore(), not just inside a useState initializer,
      // so the risk is identical). Rehydrated manually, once, in lib/providers.tsx.
      skipHydration: true,
    }
  )
);

export const useGlobalFilterStore = useDateRangeStore;
