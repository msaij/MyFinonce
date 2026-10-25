/**
 * SEBI's riskometer: six levels every Indian mutual fund must publish, set by the fund house
 * from what the fund holds and reviewed monthly. Shared by every page that shows it, so the
 * order, the colours and the wording are the same everywhere.
 */

/** Lowest first. The order is the point: sorting by the label alphabetically would put
 *  "High" before "Low" and "Very High" last only by accident. */
export const RISK_LEVELS = ["Low", "Low to Moderate", "Moderate", "Moderately High", "High", "Very High"] as const;
export type RiskLevel = (typeof RISK_LEVELS)[number];

/** One hue from light to dark (the ramp the Holdings risk tab was built on): darker reads as
 *  riskier without a legend. Ordinal, so it never needs the categorical-palette checks. */
export const RISK_RAMP = ["#fdebd9", "#fbd0a8", "#f6ab6f", "#eb6834", "#c04b16", "#8a320b"] as const;

/** 1 (Low) to 6 (Very High); null for a missing or unknown label, which sorts last. */
export function riskRank(level: unknown): number | null {
  const i = RISK_LEVELS.indexOf(String(level ?? "").trim() as RiskLevel);
  return i >= 0 ? i + 1 : null;
}

export function riskColor(level: unknown): string | null {
  const r = riskRank(level);
  return r ? RISK_RAMP[r - 1] : null;
}

export const RISK_ABOUT =
  "SEBI's riskometer: one of six levels from Low to Very High, set by the fund house from what the fund holds today (credit quality, interest-rate sensitivity and liquidity for debt; market risk for equity) and reviewed every month. Read from AMFI's fund-performance data. The same for every plan and option of a fund.";
