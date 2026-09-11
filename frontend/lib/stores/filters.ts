/**
 * Per-page-section filter persistence -- replaces filter_state.py's
 * st.session_state + data/user_filters.json seeding. See the migration
 * plan's "filter_state redesign" section: the real behavior worth keeping
 * is "filters survive a restart," which zustand's `persist` (localStorage)
 * gives per-browser -- a deliberate, acceptable narrowing from the original
 * (which also seeded a brand-new session from any browser's last save).
 *
 * Sections are namespaced exactly like the original app's filter_state
 * sections (e.g. "screener", "leaders", "compare_simulate",
 * "portfolio_weights", "portfolio_config", "portfolio_advisor") -- kept
 * deliberately separate per section, matching a confirmed behavior of the
 * original (Compare & Simulate's portfolio_weights/portfolio_config are NOT
 * shared with Portfolio Suggestion's portfolio_advisor section despite
 * similar-sounding names).
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

interface FilterSections {
  [section: string]: Record<string, unknown>;
}

interface FilterState {
  sections: FilterSections;
  getFilter: <T,>(section: string, key: string, fallback: T) => T;
  setFilter: (section: string, key: string, value: unknown) => void;
  resetSection: (section: string) => void;
}

export const useFilterStore = create<FilterState>()(
  persist(
    (set, get) => ({
      sections: {},
      getFilter: (section, key, fallback) => {
        const value = get().sections[section]?.[key];
        return value === undefined ? fallback : (value as any);
      },
      setFilter: (section, key, value) =>
        set((state) => ({
          sections: {
            ...state.sections,
            [section]: { ...state.sections[section], [key]: value },
          },
        })),
      resetSection: (section) =>
        set((state) => {
          const { [section]: _drop, ...rest } = state.sections;
          return { sections: rest };
        }),
    }),
    { name: "mf-filters" }
  )
);
