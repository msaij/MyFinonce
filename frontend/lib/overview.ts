/**
 * Pure helpers for the Overview page (Market Pulse and Alpha Leaders), kept out of the
 * component so the rules they encode are unit-tested.
 */

/** A gap between two returns is a difference in percentage points, not a percentage:
 *  "+2.40 pp", "−0.35 pp". Unicode minus so the sign never reads as a hyphen. */
export function formatPp(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "-";
  const mag = Math.abs(value).toFixed(decimals);
  if (Number(mag) === 0) return `${(0).toFixed(decimals)} pp`;
  return `${value > 0 ? "+" : "−"}${mag} pp`;
}

/** AMFI reports assets in Rs crore. A lakh crore is 1,00,000 crore, the unit the industry
 *  itself quotes its size in: "₹89.37 lakh cr", "₹1,741 cr". */
export function formatCrore(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "-";
  if (Math.abs(value) >= 100_000) return `₹${(value / 100_000).toFixed(2)} lakh cr`;
  return `₹${Math.round(value).toLocaleString("en-IN")} cr`;
}

/** "Liquid Fund (Cash & Liquid)" only where the category alone is ambiguous: "Index Funds"
 *  holds equity, debt, gold and overseas trackers, which rank as separate peer groups. */
export function peerGroupLabel(category: string | null | undefined, assetClass: string | null | undefined, ambiguous: Set<string>): string {
  const cat = category || "Uncategorised";
  return ambiguous.has(cat) && assetClass ? `${cat} (${assetClass})` : cat;
}

/** Categories that appear under more than one asset class. */
export function ambiguousCategories(rows: { peer_category?: string | null; asset_class?: string | null }[]): Set<string> {
  const classes = new Map<string, Set<string>>();
  for (const r of rows) {
    if (!r.peer_category || !r.asset_class) continue;
    if (!classes.has(r.peer_category)) classes.set(r.peer_category, new Set());
    classes.get(r.peer_category)!.add(r.asset_class);
  }
  return new Set([...classes].filter(([, s]) => s.size > 1).map(([c]) => c));
}

/**
 * One row per fund: a fund's Direct and Regular plans are the same portfolio, and listing
 * both let one fund take two of the "top 10" slots. Keeps the Direct plan (the lower-cost
 * version), else the first seen. Rows are matched on base scheme name + option.
 */
export function onePerFund<T extends { scheme_name?: string | null; option_type?: string | null; plan_type?: string | null }>(rows: T[]): T[] {
  const kept = new Map<string, T>();
  const order: string[] = [];
  for (const r of rows) {
    const key = `${(r.scheme_name ?? "").trim().toLowerCase()}||${(r.option_type ?? "").toLowerCase()}`;
    const have = kept.get(key);
    if (!have) {
      kept.set(key, r);
      order.push(key);
    } else if (have.plan_type !== "Direct" && r.plan_type === "Direct") {
      kept.set(key, r);
    }
  }
  return order.map((k) => kept.get(k)!);
}

/** Calendar days covered by the window, from the NAV dates actually used. */
export function windowDays(first: string | null | undefined, last: string | null | undefined): number | null {
  if (!first || !last) return null;
  const a = Date.parse(`${first.slice(0, 10)}T00:00:00Z`);
  const b = Date.parse(`${last.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.round((b - a) / 86_400_000);
}

/**
 * An axis range spanning the 1st to 99th percentile (padded), so one fund 25 points ahead of
 * its peers does not squash four thousand others into a line. Also says how many points fall
 * outside it, so the chart can say so -- a double-click in Plotly still shows everything.
 */
export function robustRange(values: number[], pad = 0.12): { range: [number, number]; outside: number } | null {
  const v = values.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (v.length === 0) return null;
  const at = (q: number) => v[Math.min(v.length - 1, Math.max(0, Math.round(q * (v.length - 1))))];
  let lo = Math.min(at(0.01), 0);
  let hi = Math.max(at(0.99), 0);
  if (hi === lo) {
    lo -= 1;
    hi += 1;
  }
  const span = hi - lo;
  const range: [number, number] = [lo - span * pad, hi + span * pad];
  return { range, outside: v.filter((x) => x < range[0] || x > range[1]).length };
}

/** "29 Jun" for a week starting then, "Jul 2026" for a month. */
export function periodLabel(iso: string, unit: "week" | "month" | null | undefined): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return unit === "month"
    ? d.toLocaleDateString("en-GB", { month: "short", year: "numeric", timeZone: "UTC" })
    : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

/** Fixed colour per asset class across every chart on the page, so "Debt" is never green
 *  in one chart and orange in the next. Hex because Plotly does not resolve CSS variables. */
export const ASSET_CLASS_COLORS: Record<string, string> = {
  Equity: "#2a78d6",
  "International Equity": "#7c3aed",
  Hybrid: "#d97706",
  Debt: "#0f766e",
  "Cash & Liquid": "#64748b",
  "Gold & Commodities": "#b45309",
  Other: "#94a3b8",
};
