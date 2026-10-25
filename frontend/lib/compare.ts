/**
 * Pure helpers for the Compare tab (components/compare/CompareTab.tsx), kept out of the
 * component so the rules they encode are unit-tested. The numbers themselves come from
 * GET /api/schemes/compare (backend/app/services/compare.py); these only slice, reshape
 * and word them.
 */

import type { CompareFund, CompareResult, Point } from "@/lib/api/compare";

/** Fixed categorical order (the app's chart palette, validated for colour-vision
 *  deficiency). A fund keeps its colour by its position in the selection, on every chart. */
const FUND_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];

export function fundColor(index: number): string {
  return FUND_COLORS[index % FUND_COLORS.length];
}

export type ChartMode = "growth" | "return" | "drawdown" | "rolling" | "nav";

/** Points whose date lies in [from, to], nulls dropped. ISO dates compare as strings. */
export function sliceSeries(points: Point[] | undefined, from: string, to: string): [string, number][] {
  if (!points) return [];
  return points.filter((p): p is [string, number] => p[1] !== null && p[0] >= from && p[0] <= to);
}

/** Percentage below the running peak at every point: 0 at a new high, negative below it. */
export function drawdownSeries(points: [string, number][]): [string, number][] {
  let peak = -Infinity;
  return points.map(([d, v]) => {
    peak = Math.max(peak, v);
    return [d, peak > 0 ? (v / peak - 1) * 100 : 0];
  });
}

/** One fund's line for a chart mode, over the common period (or its own window for "nav").
 *  "nav" is rebased to percentage growth from the fund's own first NAV in the window: raw
 *  NAVs of Rs 10 and Rs 1,000 on one rupee axis flatten the smaller fund into a line along
 *  the bottom, and the level itself says nothing about performance. */
export function fundLine(fund: CompareFund, mode: ChartMode, amount: number): [string, number][] {
  if (mode === "nav") {
    const own = sliceSeries(fund.series, "0000-00-00", "9999-99-99");
    if (own.length === 0 || own[0][1] <= 0) return [];
    const base = own[0][1];
    return own.map(([d, v]) => [d, (v / base - 1) * 100]);
  }
  if (mode === "rolling") return sliceSeries(fund.rolling_1y?.points, "0000-00-00", "9999-99-99");
  if (!fund.common) return [];
  const pts = sliceSeries(fund.series, fund.common.start_date, fund.common.end_date);
  if (pts.length === 0) return [];
  const base = pts[0][1];
  if (mode === "drawdown") return drawdownSeries(pts);
  if (mode === "growth") return pts.map(([d, v]) => [d, (amount * v) / base]);
  return pts.map(([d, v]) => [d, (v / base - 1) * 100]);
}

export function buildCompareFigure(funds: CompareFund[], mode: ChartMode, amount: number) {
  // The own-window view still shows the published NAV (4 dp) in the hover beside the growth.
  const hover =
    mode === "growth" ? "Rs %{y:,.2f}" : mode === "nav" ? "%{y:+.4f}% (NAV Rs %{customdata:.4f})" : "%{y:.4f}%";
  const data = funds
    .map((f, i) => ({ f, i, pts: fundLine(f, mode, amount) }))
    .filter(({ pts }) => pts.length > 0)
    .map(({ f, i, pts }) => ({
      type: "scatter",
      mode: "lines",
      name: f.display_name,
      x: pts.map((p) => p[0]),
      y: pts.map((p) => Number(p[1].toFixed(4))),
      ...(mode === "nav" ? { customdata: sliceSeries(f.series, "0000-00-00", "9999-99-99").map((p) => p[1]) } : {}),
      line: { color: fundColor(i), width: 2 },
      hovertemplate: `${f.display_name}: <b>${hover}</b><extra></extra>`,
    }));
  const pctAxis = mode !== "growth";
  return {
    data,
    layout: {
      height: 460,
      hovermode: "x unified",
      xaxis: { hoverformat: "%d %b %Y" },
      yaxis: pctAxis ? { ticksuffix: "%", showgrid: true, zeroline: true } : { showgrid: true, tickprefix: "Rs " },
      legend: { orientation: "h", yanchor: "top", y: -0.12, xanchor: "left", x: 0 },
      margin: { b: 40 + 22 * data.length },
    },
  };
}

/** "26 Jun 2026" */
export function formatDay(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
}

/** NAVs print to 4 decimals, as AMFI publishes them. */
export function formatNav(v: number | null | undefined): string {
  return v === null || v === undefined || !Number.isFinite(v) ? "-" : v.toFixed(4);
}

/** A ratio (Sharpe, Sortino, correlation): 4 decimals like every other metric in the app, signed only by its own minus. */
export function formatRatio(v: number | null | undefined, decimals = 4): string {
  return v === null || v === undefined || !Number.isFinite(v) ? "-" : v.toFixed(decimals);
}

/** A Sharpe/Sortino value, marked with a dagger when it rests on a short sample (fewer
 *  returns than the backend's short_sample threshold) -- the ratio is then noise-level. */
export function ratioWithSample(v: number | null | undefined, short: boolean): string {
  const text = formatRatio(v);
  return short && text !== "-" ? `${text} †` : text;
}

/** Hover text for one correlation cell: the value, what it was measured on, and why it is
 *  missing when it is. */
export function correlationCellTitle(
  rowName: string,
  colName: string,
  value: number | null | undefined,
  nObs: number | null | undefined,
  minObs: number,
  basis: "weekly" | "daily" | undefined
): string {
  const unit = basis === "weekly" ? "weekly" : "daily";
  const n = nObs ?? 0;
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return n < minObs
      ? `n/a: ${rowName} and ${colName} share only ${n} ${unit} returns in the common period; at least ${minObs} are needed.`
      : `n/a: ${rowName} or ${colName} shows no variation over the common period.`;
  }
  return `${rowName} vs ${colName}: ${value.toFixed(4)}, over ${n} common ${unit} returns.`;
}

/** "3 of 44" -- 1 is the best return in the peer group. */
export function rankText(rank: number | null | undefined, n: number | null | undefined): string {
  return rank && n ? `${rank} of ${n}` : "-";
}

/** Rupee fees on an amount invested at the period's start. */
export function feeInr(amount: number, feePctOfInitial: number | null | undefined): number | null {
  if (feePctOfInitial === null || feePctOfInitial === undefined || !Number.isFinite(amount)) return null;
  return (amount * feePctOfInitial) / 100;
}

/** The value of `amount` invested at the start of the common period. */
export function valueOf(amount: number, returnPct: number | null | undefined): number | null {
  if (returnPct === null || returnPct === undefined || !Number.isFinite(amount)) return null;
  return amount * (1 + returnPct / 100);
}

/** The fund with the highest (or lowest) value of `pick` among funds that have one. Ties
 *  keep the earlier fund in the selection. */
export function leader(
  funds: CompareFund[],
  pick: (f: CompareFund) => number | null | undefined,
  best: "max" | "min"
): { fund: CompareFund; value: number } | null {
  let out: { fund: CompareFund; value: number } | null = null;
  for (const f of funds) {
    const v = pick(f);
    if (v === null || v === undefined || !Number.isFinite(v)) continue;
    if (!out || (best === "max" ? v > out.value : v < out.value)) out = { fund: f, value: v };
  }
  return out;
}

/** Background tint for a correlation cell: blue for moving together, orange for moving
 *  apart, fading to white at zero -- light enough that dark text stays readable. */
export function correlationBg(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "transparent";
  const a = Math.min(Math.abs(v), 1) * 0.45;
  return v >= 0 ? `rgba(42, 120, 214, ${a.toFixed(3)})` : `rgba(235, 104, 52, ${a.toFixed(3)})`;
}

/** Funds a performance leader card may name: in the common period, and not an IDCW plan --
 *  its NAV falls by every payout, which makes its return, Sharpe and drawdown say less
 *  about the fund than the Growth option's would (a payout reads as a drawdown). */
export function performanceLeaderPool(funds: CompareFund[]): CompareFund[] {
  return funds.filter((f) => f.status === "ok" && !!f.common && !f.is_idcw);
}

/** Funds the "Lowest TER" card may name: an official AMFI TER on a scheme that is still
 *  publishing. A wound-up plan keeps its last disclosed TER in the database, but it is not
 *  a cost anyone can pay today. */
export function terLeaderPool(funds: CompareFund[]): CompareFund[] {
  return funds.filter((f) => f.ter_status === "official" && f.is_active !== false && f.status !== "stale" && f.status !== "not_found");
}

function daysBetween(fromIso: string, toIso: string): number | null {
  const a = Date.parse(`${fromIso}T00:00:00Z`);
  const b = Date.parse(`${toIso}T00:00:00Z`);
  return Number.isFinite(a) && Number.isFinite(b) ? Math.round((b - a) / 86_400_000) : null;
}

function joinNames(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** Plain-language account of the period the funds are compared over and why it is that
 *  period -- the first thing on the tab, because every number below depends on it. */
export function periodExplanation(result: CompareResult): { level: "info" | "warning"; text: string }[] {
  const out: { level: "info" | "warning"; text: string }[] = [];
  const byCode = new Map(result.funds.map((f) => [f.scheme_code, f]));
  const name = (c: number) => byCode.get(c)?.display_name ?? String(c);
  const c = result.common;
  if (!c) {
    const usable = result.funds.filter((f) => f.status === "ok").length;
    out.push({
      level: "warning",
      text:
        usable === 0
          ? "None of the selected funds has two or more NAVs in this window, so there is nothing to compare. Widen the time horizon."
          : "The selected funds have no stretch of this window in which all of them were publishing NAVs, so they cannot be compared like for like. Widen the window or remove the fund that starts last.",
    });
  } else {
    out.push({
      level: c.coverage_pct < 75 ? "warning" : "info",
      text:
        `Every figure below that says "common period" runs from ${formatDay(c.start)} to ${formatDay(c.end)} ` +
        `(${c.days} days, ${c.coverage_pct.toFixed(2)}% of the selected window), valued at each fund's NAV as of those dates.`,
    });
    if (c.limited_end_by.length) {
      // Say how far behind: a day's publication lag (an overseas FoF) and a fund that
      // matured three weeks ago both set the end, but only one of them is worth removing.
      const lag = c.latest_end ? daysBetween(c.end, c.latest_end) : null;
      const behind = lag && lag > 0 ? `, ${lag} day${lag === 1 ? "" : "s"} before the newest NAV among the selected funds (${formatDay(c.latest_end)})` : "";
      out.push({
        level: lag !== null && lag > 7 ? "warning" : "info",
        text: `${joinNames(c.limited_end_by.map(name))} has no NAV after ${formatDay(c.end)}${behind}, so the period ends there for every fund: a return that includes a day another fund's does not is not a like-for-like comparison.${lag !== null && lag > 7 ? " A gap this long usually means the fund has stopped publishing (matured, merged or wound up); remove it to compare the others to their latest NAV." : ""}`,
      });
    }
    if (c.limited_start_by.length) {
      const first = c.limited_start_by.map((code) => `${name(code)} (first NAV ${formatDay(byCode.get(code)?.own_window?.start_date)})`);
      out.push({
        level: "warning",
        text: `${joinNames(first)} began publishing after the window opened, so the period starts on ${formatDay(c.start)} for every fund. Remove it to compare the others over the whole window.`,
      });
    }
  }
  for (const f of result.funds) {
    if (f.status !== "ok" && f.status_note) out.push({ level: "warning", text: `${f.display_name}: ${f.status_note}` });
  }
  const idcw = result.funds.filter((f) => f.is_idcw).map((f) => f.display_name);
  if (idcw.length) {
    out.push({
      level: "warning",
      text: `${joinNames(idcw)} ${idcw.length > 1 ? "are IDCW plans" : "is an IDCW plan"}: the NAV drops by every payout, so its NAV return, drawdown and ratios understate what a holder actually earned. Compare the Growth option of the same fund for performance.`,
    });
  }
  return out;
}
