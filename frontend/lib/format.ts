/**
 * TS port of fetcher/theme.py's format_signed_pct() and tone_class() -- these
 * have a load-bearing contract (no double-sign bug: "+-2.13%" never appears)
 * that any future formatter must replicate exactly. See the migration plan.
 */

/**
 * The one "muted/neutral" color for de-emphasized Plotly chart elements (a
 * secondary reference line, a low-key quadrant marker) that ISN'T a plain DOM
 * element -- `var(--mf-muted)` looks like the obvious choice, but it silently
 * does NOT work here: Plotly parses a trace/shape `color` string itself (to
 * derive hover/legend swatch variants) rather than handing it to the DOM as
 * an inline style, and its color parser doesn't understand CSS `var()` --
 * passing it in doesn't error, it just silently falls back to Plotly's own
 * default categorical color for that trace (confirmed directly: a test trace
 * given `color: "var(--mf-muted)"` rendered as an unrelated default green).
 * `layout.font.color` is the one Plotly-JSON color that DOES resolve
 * `var(...)` correctly (Plotly does apply that one via inline style), which
 * is why axis/tick/legend/annotation text already theme correctly without
 * this constant -- this is only for trace lines/markers/shapes.
 *
 * Matches `--mf-muted` exactly (`#475569`, ~7.6:1 against this app's white
 * background) now that dark mode has been removed entirely (2026-09-12) --
 * there's only one background to clear a contrast minimum against, so this
 * no longer needs to be a dual-theme compromise value. (An earlier version
 * of this constant used `#64748B`, chosen to also stay legible against a
 * dark background that no longer exists.)
 */
export const CHART_MUTED_COLOR = "#475569";

export function formatSignedPct(value: number | null | undefined, decimals = 4): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  const mag = Math.abs(value).toFixed(decimals);
  // Signed by what is printed: -0.0013 at 2 decimals is "0.00%", never "-0.00%".
  const sign = Number(mag) === 0 ? "" : value > 0 ? "+" : "-";
  return `${sign}${mag}%`;
}

export type Tone = "pos" | "neg" | "neutral";

export function toneOf(value: number | null | undefined): Tone {
  if (value === null || value === undefined || Number.isNaN(value)) return "neutral";
  if (value > 0) return "pos";
  if (value < 0) return "neg";
  return "neutral";
}

const inr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

export function formatInr(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return inr.format(value);
}

const inrWhole = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });

/** Whole rupees, for estimates where paise would be false precision: "₹913", not "₹913.00". */
export function formatInrWhole(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return inrWhole.format(value);
}

/** Lakh/crore shorthand for a tile that has no room for every digit: "₹15.5L", "₹1.2Cr". */
export function formatInrShort(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (abs >= 1e7) return `${sign}₹${(abs / 1e7).toFixed(1)}Cr`;
  if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(1)}L`;
  return `${sign}${inrWhole.format(abs)}`;
}

export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return "-";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}
