/**
 * Chart tokens for the Holdings page. Plotly parses trace colours itself and
 * does not resolve CSS var() (see CHART_MUTED_COLOR in lib/format.ts), so these
 * are hex.
 *
 * SERIES_1/SERIES_2 are the dataviz reference palette's first two categorical
 * slots, validated against this app's white surface (light-only since
 * 2026-09-12): CVD dE 24.7, normal-vision dE 33.6, both >= 3:1 contrast.
 * Colour follows the entity: SERIES_1 is always "your portfolio", SERIES_2 is
 * always the comparison (benchmark / withdrawals). GAIN/LOSS are the app's own
 * --mf-success / --mf-danger, and every gain/loss mark also carries a signed
 * label so colour is never the only cue.
 */
import { CHART_MUTED_COLOR } from "./format";

export const SERIES_1 = "#2a78d6";
export const SERIES_2 = "#eb6834";
export const GAIN = "#047857";
export const LOSS = "#b91c1c";
export const REFERENCE = CHART_MUTED_COLOR;
export const GRID = "#e1e0d9";

export const AXIS = { gridcolor: GRID, zerolinecolor: "#c3c2b7", linecolor: "#c3c2b7" } as const;

export function signedTone(v: number): string {
  return v >= 0 ? GAIN : LOSS;
}
