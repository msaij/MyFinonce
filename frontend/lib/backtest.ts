/**
 * Pure helpers for the Compare & Simulate page's Portfolio Backtest tab
 * (components/compare/BacktestPanel.tsx). No React, no fetching -- tested in backtest.test.ts.
 */

/** The dataviz reference palette's eight categorical slots (light surface), in its fixed
 * order. A fund keeps its slot by its position in the selection, never by its rank. */
export const FUND_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"] as const;

export function fundColor(index: number): string {
  return FUND_COLORS[index % FUND_COLORS.length];
}

/** "122639:60,118989:40" -> {122639: 60, 118989: 40}; malformed pairs are skipped. */
export function parseWeightsParam(param: string | null | undefined): Record<number, number> {
  const out: Record<number, number> = {};
  if (!param) return out;
  for (const pair of param.split(",")) {
    const [k, v] = pair.split(":");
    const code = Number(k);
    const val = Number(v);
    if (k && v !== undefined && v !== "" && Number.isFinite(code) && code > 0 && Number.isFinite(val)) out[code] = val;
  }
  return out;
}

/** Only the selected funds' weights -- a removed fund's old weight must not linger in the URL. */
export function serializeWeights(codes: number[], weights: Record<number, number>): string | undefined {
  if (codes.length === 0) return undefined;
  return codes.map((c) => `${c}:${weights[c] ?? 0}`).join(",");
}

export function equalWeights(codes: number[]): Record<number, number> {
  const out: Record<number, number> = {};
  if (codes.length === 0) return out;
  const each = Number((100 / codes.length).toFixed(2));
  for (const c of codes) out[c] = each;
  return out;
}

/**
 * The weight each selected fund is simulated with. A fund added after the weights were set
 * has no weight of its own; it gets an equal share (100 / n) instead of the silent 0% it
 * used to get, which dropped it from the backtest without a word.
 */
export function effectiveWeights(codes: number[], weights: Record<number, number>): Record<number, number> {
  const fallback = codes.length ? Number((100 / codes.length).toFixed(2)) : 0;
  const out: Record<number, number> = {};
  for (const c of codes) {
    const w = weights[c];
    out[c] = w === undefined || w === null || !Number.isFinite(w) ? fallback : w;
  }
  return out;
}

export interface WeightSummary {
  sum: number;
  /** Each fund's share of the total, in % (what the simulation actually uses). */
  normalized: Record<number, number>;
  hasNegative: boolean;
  /** True when the entered weights do not already add up to 100 (within 0.5). */
  rescaled: boolean;
}

export function summarizeWeights(codes: number[], weights: Record<number, number>): WeightSummary {
  const sum = codes.reduce((s, c) => s + (weights[c] ?? 0), 0);
  const hasNegative = codes.some((c) => (weights[c] ?? 0) < 0);
  const normalized: Record<number, number> = {};
  for (const c of codes) normalized[c] = sum > 0 ? ((weights[c] ?? 0) / sum) * 100 : 0;
  return { sum, normalized, hasNegative, rescaled: sum > 0 && Math.abs(sum - 100) > 0.5 };
}

const DAY_MS = 86_400_000;

function toDay(iso: string): number {
  return Math.floor(Date.parse(iso.slice(0, 10) + "T00:00:00Z") / DAY_MS);
}

/**
 * Trailing-window return of a level series (the TWR index) at every date that has a full
 * window behind it: level_t / level_base - 1, where base is the last point on or before
 * t - windowDays. Windows longer than a year are annualised over their actual day count.
 * Returns percentages.
 */
export function rollingReturns(dates: string[], levels: number[], windowDays: number): { x: string[]; y: number[] } {
  const x: string[] = [];
  const y: number[] = [];
  if (dates.length !== levels.length || dates.length < 2 || windowDays <= 0) return { x, y };
  const days = dates.map(toDay);
  let j = 0;
  for (let i = 0; i < dates.length; i++) {
    const target = days[i] - windowDays;
    if (target < days[0]) continue;
    while (j + 1 < i && days[j + 1] <= target) j++;
    const base = levels[j];
    if (!(base > 0)) continue;
    const r = levels[i] / base - 1;
    const span = days[i] - days[j];
    const val = windowDays > 366 ? Math.pow(1 + r, 365.25 / span) - 1 : r;
    x.push(dates[i]);
    y.push(val * 100);
  }
  return { x, y };
}

/** Why an XIRR is missing, in words. */
export function xirrNoteText(note: string | null | undefined, minDays = 30): string {
  if (note === "too_short") return `Withheld: under ${minDays} days`;
  if (note === "no_solution") return "No stable solution for these cash flows";
  return "Not available";
}

export function ordinal(n: number): string {
  const s = n % 100;
  if (s >= 11 && s <= 13) return `${n}th`;
  return `${n}${n % 10 === 1 ? "st" : n % 10 === 2 ? "nd" : n % 10 === 3 ? "rd" : "th"}`;
}

/**
 * Choices for the SIP date control: "start" = the window's start day, then 1..30 and month
 * end. 29 and 30 used to be missing although the backend accepts them, so a shared link with
 * sip_day=30 showed an empty control.
 */
export const SIP_DAY_CHOICES: ("start" | number)[] = ["start", ...Array.from({ length: 30 }, (_, i) => i + 1), 31];

export function sipDayLabel(day: "start" | number): string {
  if (day === "start") return "Same day as the start date";
  if (day === 31) return "Last day of the month";
  if (day >= 29) return `${ordinal(day)} of every month (month end in shorter months)`;
  return `${ordinal(day)} of every month`;
}

/** Unsigned percentage (weights, shares) -- a weight is never "+". */
export function formatShare(v: number | null | undefined, decimals = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "-";
  return `${v.toFixed(decimals)}%`;
}
