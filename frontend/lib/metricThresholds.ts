/**
 * Color-ladder threshold tables for StatCard's `thresholds` prop -- unifies
 * the Quant Analysis page's per-metric multi-threshold conditional coloring
 * (e.g. Sharpe: >1.0 green / >0.5 blue / >=0 amber / else red) into one
 * reusable shape instead of ~150 lines of duplicated inline HTML per card.
 *
 * NOTE: only Sharpe's exact bands are confirmed from the original page
 * (fetcher/pages/4_Quantitative_MF_Analysis.py) so far -- the others below
 * are placeholders using the same 4-band shape, to be replaced with the
 * exact original thresholds when that page is actually ported in Phase 8.
 * Do not treat the non-Sharpe values here as verified.
 */

export interface ThresholdBand {
  min: number;
  color: "success" | "accent" | "warning" | "danger";
}

export const SHARPE_THRESHOLDS: ThresholdBand[] = [
  { min: 1.0, color: "success" },
  { min: 0.5, color: "accent" },
  { min: 0, color: "warning" },
  { min: -Infinity, color: "danger" },
];

// Placeholders -- confirm exact bands against the original page in Phase 8.
export const SORTINO_THRESHOLDS: ThresholdBand[] = SHARPE_THRESHOLDS;
export const BETA_THRESHOLDS: ThresholdBand[] = SHARPE_THRESHOLDS;
export const ALPHA_THRESHOLDS: ThresholdBand[] = SHARPE_THRESHOLDS;
export const CALMAR_THRESHOLDS: ThresholdBand[] = SHARPE_THRESHOLDS;

export function colorForValue(value: number | null | undefined, bands: ThresholdBand[]): ThresholdBand["color"] {
  if (value === null || value === undefined || Number.isNaN(value)) return "warning";
  const sorted = [...bands].sort((a, b) => b.min - a.min);
  for (const band of sorted) {
    if (value >= band.min) return band.color;
  }
  return sorted[sorted.length - 1]?.color ?? "warning";
}
