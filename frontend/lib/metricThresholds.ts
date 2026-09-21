/**
 * Color-ladder threshold tables for the Quant Analysis page's StatCards --
 * confirmed against the ORIGINAL page source
 * (fetcher/pages/4_Quantitative_MF_Analysis.py) during Phase 8, not
 * invented. Bands use StatCard's own tone vocabulary directly ("pos" /
 * "accent" / "warn" / "neg") so colorForValue()'s result passes straight
 * into StatCard's `tone` prop with no extra mapping layer.
 *
 * Only Sharpe and Sortino have a real multi-tier system in the source.
 * Beta and Calmar have NO conditional coloring at all there (plain value
 * display) -- don't invent one. Alpha uses simple positive/negative/
 * unavailable coloring, which is exactly lib/format.ts's existing
 * toneOf() helper -- no separate threshold table needed for it either.
 */

import { Tone } from "./format";

export interface ThresholdBand {
  min: number;
  tone: Tone | "warn" | "accent";
}

/** Sharpe Ratio: >1.0 green / >0.5 blue / >=0 amber / else red. */
export const SHARPE_THRESHOLDS: ThresholdBand[] = [
  { min: 1.0, tone: "pos" },
  { min: 0.5, tone: "accent" },
  { min: 0, tone: "warn" },
  { min: -Infinity, tone: "neg" },
];

/** Sortino Ratio: >1.5 green / >0.8 blue / else red -- deliberately only 3
 * tiers, no amber -- this is NOT the same shape as Sharpe's ladder. */
export const SORTINO_THRESHOLDS: ThresholdBand[] = [
  { min: 1.5, tone: "pos" },
  { min: 0.8, tone: "accent" },
  { min: -Infinity, tone: "neg" },
];

export function colorForValue(value: number | null | undefined, bands: ThresholdBand[]): ThresholdBand["tone"] {
  if (value === null || value === undefined || Number.isNaN(value)) return "neutral";
  const sorted = [...bands].sort((a, b) => b.min - a.min);
  for (const band of sorted) {
    if (value >= band.min) return band.tone;
  }
  return sorted[sorted.length - 1]?.tone ?? "neutral";
}
