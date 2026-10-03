import type { NseBidRow, NseDemandGraph, NseIssueDetail } from "@/lib/api/nseIpo";

/**
 * A quick read of an IPO from what NSE publishes: who is bidding, how hard, at what price,
 * how big the issue is and where its money goes. Worked out in the browser each time the
 * panel opens; nothing is kept.
 *
 * It reads demand and terms only. It knows nothing of the company's financials, its valuation
 * against listed peers, or the grey-market premium, which is why the verdict is a nudge and
 * not advice.
 */

export type Tone = "pos" | "neg" | "warn" | "neutral";

export interface SignalTile {
  label: string;
  value: string;
  sub: string;
  tone: Tone;
  about: string;
}

export interface Verdict {
  label: string;
  tone: Tone;
  reasons: string[];
  /** When NSE's bid figures were taken, as NSE words it. */
  asOf: string | null;
}

/** What the listing row already knew about the issue. */
export interface IssueContext {
  status: string | null;
  issue_start: string | null;
  issue_end: string | null;
  series: string | null;
  subscription_times?: number | null;
  /** Shares open to bidders (anchor book excluded); for SME rows NSE gives only shares offered. */
  issue_size?: number | null;
  shares_offered?: number | null;
}

const x = (v: number) => `${v >= 100 ? v.toFixed(0) : v.toFixed(2)}x`;
const crore = (rupees: number) => `Rs ${Math.round(rupees / 1e7).toLocaleString("en-IN")} cr`;

// --- bid table ----------------------------------------------------------------------------

/** A category's subscription, or null when NSE publishes no reserved quantity for it
 *  (an SME issue bid on BSE shows 0 offered everywhere on NSE -- that is "no data", not "no demand"). */
function times(rows: NseBidRow[], match: (r: NseBidRow) => boolean): number | null {
  const r = rows.find(match);
  if (!r || !r.shares_offered || r.subscription_times == null) return null;
  return r.subscription_times;
}

const isQib = (r: NseBidRow) => r.sr_no === "1" || /^qualified institutional/i.test(r.category);
const isNii = (r: NseBidRow) => r.sr_no === "2" || /^non institutional investors$/i.test(r.category.trim());
// Mainboard: "Retail Individual Investors(RIIs)"; SME: "Individual Investors".
const isRetail = (r: NseBidRow) => /^(retail individual|individual investors)/i.test(r.category.trim());
const isTotal = (r: NseBidRow) => r.category.trim().toLowerCase() === "total";
/** Top-level rows beyond QIB / NII / retail: Employees, Shareholders, Policyholders. */
const isSpecialQuota = (r: NseBidRow) => !!r.sr_no && /^\d+$/.test(r.sr_no) && !isQib(r) && !isNii(r) && !isRetail(r);

/** Consolidated (all exchanges) when it carries reserved quantities, else NSE's own table. */
function bidRows(d: NseIssueDetail): NseBidRow[] {
  const consolidatedTotal = d.bid_details_consolidated.find(isTotal);
  return consolidatedTotal?.shares_offered ? d.bid_details_consolidated : d.bid_details_nse;
}

// --- issue terms --------------------------------------------------------------------------

export function firstInt(s: string | undefined | null): number | null {
  const m = s?.replace(/,/g, "").match(/\d+/);
  return m ? Number(m[0]) : null;
}

/** Upper end of a price band: "Rs. 258 to Rs. 272 per Equity Share" -> 272; "Rs. 50" -> 50. */
export function upperPrice(s: string | undefined | null): number | null {
  const nums = (s?.replace(/,/g, "").match(/\d+(\.\d+)?/g) ?? []).map(Number).filter((n) => n > 0);
  return nums.length ? Math.max(...nums) : null;
}

const RUPEES: Record<string, number> = { million: 1e6, mn: 1e6, crore: 1e7, crores: 1e7, cr: 1e7, lakh: 1e5, lakhs: 1e5, lac: 1e5, lacs: 1e5 };

/** The first amount in a clause, in rupees. NSE states each part either in money
 *  ("Rs. 3,200 million", "Rs. 29,000 lakhs", "2870 million") or in shares ("10,00,000 Equity
 *  Shares", or a bare "7,14,000"); shares are valued at the top of the band. */
function clauseRupees(clause: string, price: number | null): number | null {
  const m = clause.match(/(\d[\d,]*(?:\.\d+)?)\s*(million|mn|crores?|cr|lakhs?|lacs?)?/i);
  if (!m) return null;
  const n = Number(m[1].replace(/,/g, ""));
  if (!Number.isFinite(n) || n <= 0) return null;
  const unit = m[2]?.toLowerCase();
  if (unit) return n * RUPEES[unit];
  return price ? n * price : null;
}

export interface Composition {
  fresh: number | null;
  ofs: number | null;
  anchorShares: number | null;
  employeeQuota: boolean;
}

/** Splits NSE's free-text Issue Size line into the fresh issue and the offer for sale.
 *  The fresh part is whatever amount precedes "offer for sale" -- NSE sometimes leaves out
 *  the words "fresh issue" ("comprising of aggregating up to 7500 million and offer for sale"). */
export function composition(text: string, price: number | null): Composition {
  const main = text.split("(")[0];
  const ofsAt = main.search(/offer\s+for\s+sale/i);
  const freshClause = ofsAt >= 0 ? main.slice(0, ofsAt) : main;
  const ofsClause = ofsAt >= 0 ? main.slice(ofsAt) : "";
  const anchor = text.match(/anchor[^\d]{0,40}?(\d[\d,]*)\s*equity/i);
  return {
    fresh: clauseRupees(freshClause, price),
    ofs: ofsClause ? clauseRupees(ofsClause, price) : null,
    anchorShares: anchor ? Number(anchor[1].replace(/,/g, "")) : null,
    employeeQuota: /employee/i.test(text),
  };
}

const info = (d: NseIssueDetail, ...titles: RegExp[]) => {
  for (const t of titles) {
    const hit = d.issue_info.find((i) => i.title && t.test(i.title));
    if (hit) return hit.value;
  }
  return null;
};

// --- time ---------------------------------------------------------------------------------

function todayIST(now: Date = new Date()): string {
  return now.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
}

/** Weekdays from `from` to `to`, both included. Exchange holidays are not known here. */
export function biddingDays(from: string, to: string): number {
  let n = 0;
  for (let t = Date.parse(from); t <= Date.parse(to); t += 86400000) {
    const wd = new Date(t).getUTCDay();
    if (wd !== 0 && wd !== 6) n++;
  }
  return n;
}

const shortDate = (iso: string) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });

type Stage = "upcoming" | "open" | "last-day" | "closed";

export function stageOf(ctx: IssueContext, today: string): Stage {
  if ((ctx.status ?? "").toLowerCase() === "forthcoming" || (ctx.issue_start && ctx.issue_start > today)) return "upcoming";
  if ((ctx.status ?? "").toLowerCase() === "closed" || (ctx.issue_end && ctx.issue_end < today)) return "closed";
  return ctx.issue_end === today ? "last-day" : "open";
}

// --- demand graph -------------------------------------------------------------------------

/** Share of all bids placed at the top of the band or at cut-off. */
export function topOfBandShare(g: NseDemandGraph | null): number | null {
  const pts = (g?.points ?? []).filter((p) => p.qty_lakh != null);
  const total = pts.reduce((s, p) => s + (p.qty_lakh as number), 0);
  if (!pts.length || total <= 0) return null;
  const prices = pts.map((p) => Number(p.price)).filter((n) => Number.isFinite(n));
  const top = prices.length ? Math.max(...prices) : null;
  const atTop = pts
    .filter((p) => /cut/i.test(p.price) || (top != null && Number(p.price) === top))
    .reduce((s, p) => s + (p.qty_lakh as number), 0);
  return atTop / total;
}

function demandTone(v: number | null, strong: number, ok: number): Tone {
  if (v == null) return "neutral";
  if (v >= strong) return "pos";
  if (v >= ok) return "neutral";
  return v < 1 ? "neg" : "warn";
}

const demandWord = (v: number | null) => (v == null ? "Not published" : v >= 10 ? "Strong" : v >= 3 ? "Healthy" : v >= 1 ? "Just covered" : "Not yet covered");

// --- the read -----------------------------------------------------------------------------

export function ipoSignals(d: NseIssueDetail, ctx: IssueContext, today: string = todayIST()): { verdict: Verdict; tiles: SignalTile[] } {
  const rows = bidRows(d);
  const stage = stageOf(ctx, today);
  const sme = (ctx.series ?? d.series ?? "").toUpperCase() === "SME";

  const qib = times(rows, isQib);
  const nii = times(rows, isNii);
  const retail = times(rows, isRetail);
  const overall = times(rows, isTotal) ?? ctx.subscription_times ?? null;
  const quotas = rows.filter(isSpecialQuota);
  const graph = d.demand_graph_all?.points.length ? d.demand_graph_all : d.demand_graph_nse;
  const topShare = topOfBandShare(graph);

  const lot = firstInt(info(d, /^bid lot/i, /^lot size/i, /^minimum order quantity/i));
  const price = upperPrice(info(d, /^price range/i));
  const comp = composition(info(d, /^issue size/i) ?? "", price);
  const discount = info(d, /^discount/i);
  const hasDiscount = !!discount && !/^na$/i.test(discount.trim());

  const bidderShares = ctx.issue_size ?? ctx.shares_offered ?? null;
  const fromParts = comp.fresh != null || comp.ofs != null ? (comp.fresh ?? 0) + (comp.ofs ?? 0) : null;
  const totalRupees = fromParts ?? (bidderShares && price ? (bidderShares + (comp.anchorShares ?? 0)) * price : null);
  const anchorRupees = comp.anchorShares && price ? comp.anchorShares * price : null;
  const ofsShare = fromParts ? (comp.ofs ?? 0) / fromParts : null;

  // Cautions that hold whatever the demand turns out to be.
  const cautions: string[] = [];
  if (ofsShare != null && ofsShare >= 0.999) cautions.push("Pure offer for sale: the company raises nothing; existing holders are cashing out");
  else if (ofsShare != null && ofsShare >= 0.5) cautions.push(`${Math.round(ofsShare * 100)}% of the issue is existing holders selling out`);
  if (sme) cautions.push("SME issue: smaller company, thin trading after listing, higher risk");

  // --- verdict -----------------------------------------------------------------------------
  const asOf = d.consolidated_updated ?? graph?.timestamp ?? null;
  let verdict: Verdict;
  if (stage === "upcoming") {
    verdict = {
      label: "Too early to tell",
      tone: "neutral",
      reasons: [
        `Bidding opens ${ctx.issue_start ? shortDate(ctx.issue_start) : "soon"}; there is no demand to read yet. Check back on the last bidding day.`,
        ...cautions,
      ],
      asOf: null,
    };
  } else if (overall == null && qib == null && nii == null) {
    verdict = { label: "No demand data", tone: "neutral", reasons: ["NSE publishes no subscription figures for this issue.", ...cautions], asOf };
  } else {
    const reasons: string[] = [];
    let score = 0;
    const early = stage === "open";
    const add = (v: number | null, name: string, strong: number, ok: number) => {
      if (v == null) return;
      if (v >= strong) {
        score += 2;
        reasons.push(`${name} ${x(v)}: strong`);
      } else if (v >= ok) {
        score += 1;
        reasons.push(`${name} ${x(v)}: healthy`);
      } else if (v < 1 && !early) {
        score -= 2;
        reasons.push(`${name} ${x(v)}: not even fully subscribed`);
      } else {
        reasons.push(`${name} ${x(v)}: soft${early ? " so far" : ""}`);
      }
    };
    add(qib, "Institutions (QIB)", 10, 3);
    add(nii, "HNIs (NII)", 10, 3);
    add(overall, "Overall", 20, 5);
    if (sme) score -= 1;
    if (ofsShare != null && ofsShare >= 0.999) score -= 1;
    if (early && qib != null && qib < 1) reasons.push("Institutions usually bid on the last day");

    const all = [...reasons, ...cautions];
    if (stage === "closed") {
      verdict =
        score >= 5
          ? { label: "Closed: demand was strong", tone: "pos", reasons: all, asOf }
          : score >= 2
            ? { label: "Closed: demand was moderate", tone: "warn", reasons: all, asOf }
            : { label: "Closed: demand was weak", tone: "neg", reasons: all, asOf };
      verdict.reasons.unshift("Bidding is over, so you can no longer apply");
    } else if (score >= 5) verdict = { label: "Worth a shot", tone: "pos", reasons: all, asOf };
    else if (score >= 2) verdict = { label: "Borderline: small bet at most", tone: "warn", reasons: all, asOf };
    else if (early)
      verdict = {
        label: "Too early to tell",
        tone: "neutral",
        reasons: [...all.filter((r) => !r.startsWith("Institutions usually")), "Institutions and HNIs mostly bid on the last day; early numbers understate demand"],
        asOf,
      };
    else verdict = { label: "Consider skipping", tone: "neg", reasons: all, asOf };
  }

  // --- tiles -------------------------------------------------------------------------------
  const tiles: SignalTile[] = [];

  const left = ctx.issue_end ? biddingDays(today, ctx.issue_end) : null;
  const timing =
    stage === "upcoming"
      ? { value: ctx.issue_start ? `Opens ${shortDate(ctx.issue_start)}` : "Not open", sub: ctx.issue_end ? `Closes ${shortDate(ctx.issue_end)}` : "Dates not published", tone: "neutral" as Tone }
      : stage === "closed"
        ? { value: "Closed", sub: "Figures are final", tone: "neutral" as Tone }
        : stage === "last-day"
          ? { value: "Last day", sub: "Closes 5 PM; confirm the UPI mandate by then", tone: "warn" as Tone }
          : { value: `${left} bidding day${left === 1 ? "" : "s"} left`, sub: `Closes ${shortDate(ctx.issue_end as string)}`, tone: "neutral" as Tone };
  tiles.push({
    label: "Timing",
    ...timing,
    about: "Where the issue is in its bidding window, counted in weekdays (exchange holidays are not known here). Institutions and HNIs typically bid late, so day 1 or 2 subscription is a weak guide to the final number.",
  });

  tiles.push({
    label: "Institutions (QIB)",
    value: qib == null ? "-" : x(qib),
    sub: qib == null ? (sme ? "Not published for this SME issue" : "Not published") : demandWord(qib),
    tone: stage === "open" && qib != null && qib < 1 ? "neutral" : demandTone(qib, 10, 3),
    about: "Subscription of the Qualified Institutional Buyers portion: mutual funds, insurers, FIIs and banks, who do the deepest research. Historically the best single hint of a strong listing. They usually bid on the last day.",
  });
  tiles.push({
    label: "HNIs (NII)",
    value: nii == null ? "-" : x(nii),
    sub: nii == null ? (sme ? "Not published for this SME issue" : "Not published") : demandWord(nii),
    tone: stage === "open" && nii != null && nii < 1 ? "neutral" : demandTone(nii, 10, 3),
    about: "Subscription of the Non-Institutional Investors portion (bids above Rs 2 lakh). Often leveraged money chasing listing gains, so it tracks sentiment closely.",
  });
  tiles.push({
    label: "Retail",
    value: retail == null ? "-" : x(retail),
    sub: retail == null ? "Not published" : retail > 1.5 ? `Allotment odds about 1 in ${Math.round(retail)} or better` : "Good chance of allotment",
    tone: retail == null ? "neutral" : retail > 1.5 ? "warn" : "pos",
    about: "Subscription of the Retail Individual Investors portion (up to Rs 2 lakh). When oversubscribed, SEBI rules allot one minimum lot to as many applicants as possible by lottery. The odds shown assume every applicant bid for one lot; since many bid for more, the real odds are usually a little better.",
  });

  if (quotas.length || hasDiscount) {
    const parts = quotas.map((q) => `${q.category.replace(/\(.*\)/, "").trim()} ${q.shares_offered && q.subscription_times != null ? x(q.subscription_times) : "-"}`);
    tiles.push({
      label: "Special quotas",
      value: parts.join(", ") || "Employees",
      sub: hasDiscount ? (discount as string) : "Reserved portion; no discount",
      tone: "neutral",
      about: "Portions reserved for the company's employees or its parent's shareholders, with their own subscription and sometimes a discount. Only relevant if you qualify.",
    });
  }

  tiles.push({
    label: "Issue size",
    value: totalRupees ? crore(totalRupees) : "-",
    sub: anchorRupees ? `incl. ${crore(anchorRupees)} anchor book` : totalRupees ? "No anchor book stated" : "Not stated",
    tone: "neutral",
    about:
      "The whole issue at the top of the price band, from NSE's Issue Size line (shares valued at the upper price). Anchor investors are institutions allotted shares the day before bidding opens, with a lock-in. A large issue (over Rs 1,000 cr) needs deep demand to get oversubscribed; a small one gets there easily, so compare subscription with size.",
  });
  tiles.push({
    label: "Where the money goes",
    value: ofsShare == null ? (comp.fresh ? "Fresh issue" : "-") : ofsShare >= 0.999 ? "100% OFS" : ofsShare <= 0.001 ? "100% fresh" : `${Math.round((1 - ofsShare) * 100)}% fresh · ${Math.round(ofsShare * 100)}% OFS`,
    sub:
      comp.fresh != null && comp.ofs != null
        ? `${crore(comp.fresh)} to the company, ${crore(comp.ofs)} to sellers`
        : comp.ofs != null
          ? "All to existing shareholders"
          : comp.fresh != null
            ? "All to the company"
            : "Not stated",
    tone: ofsShare == null ? "neutral" : ofsShare >= 0.999 ? "neg" : ofsShare >= 0.5 ? "warn" : "neutral",
    about: "A fresh issue raises money for the company to grow or repay debt. An offer for sale (OFS) only pays existing shareholders (promoters, private-equity funds) who are selling. A large OFS share is not bad by itself, but it means insiders are taking money off the table.",
  });
  tiles.push({
    label: "Minimum application",
    value: lot && price ? `Rs ${(lot * price).toLocaleString("en-IN")}` : "-",
    sub: lot && price ? `${lot.toLocaleString("en-IN")} shares × Rs ${price.toLocaleString("en-IN")}` : "Lot or price not published",
    tone: "neutral",
    about: sme
      ? "One lot at the top of the price band. SME lots are large; check the prospectus for how many lots an individual must apply for."
      : "One lot at the top of the price band: the least you can apply for. Retail applicants may bid up to Rs 2 lakh; bidding at cut-off means you accept whatever final price is set.",
  });
  tiles.push({
    label: "Bids at top price",
    value: topShare == null ? "-" : `${Math.round(topShare * 100)}%`,
    sub: topShare == null ? "No bids plotted yet" : topShare >= 0.9 ? "Typical: bidders take the top price" : "Unusually price-sensitive bidding",
    tone: topShare == null || topShare >= 0.9 ? "neutral" : "warn",
    about: "Share of all shares bid at the upper end of the band or at cut-off, from the demand graph. Almost every issue sits above 90% because retail bids at cut-off, so this only means something when it is low: then bidders doubt the top price.",
  });
  tiles.push({
    label: "Board",
    value: sme ? "SME (Emerge)" : "Mainboard",
    sub: sme ? "Higher risk, thin liquidity" : "Main NSE board",
    tone: sme ? "warn" : "neutral",
    about: "SME issues list on NSE Emerge: smaller companies, lighter disclosure, large minimum lots and often thin trading after listing, so exits can be hard.",
  });

  return { verdict, tiles };
}
