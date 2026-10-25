/**
 * Formatting for the news feed: times in IST (the app's convention, see lib/format.ts),
 * relative labels by IST calendar day, category/region labels, sentiment styles, and
 * feed-health labels.
 *
 * No favicons: fetching them from a third party (Google's s2 service, as before) told it
 * which publishers were on screen. Cards show the source's initial instead.
 */

import { NEWS_CATEGORIES, NEWS_REGION_LABELS, type FeedState, type NewsRegionSlug } from "@/components/news/types";

const IST = "Asia/Kolkata";
// Fixed names rather than the locale's: ICU spells September "Sept" in some versions.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const istFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: IST,
  year: "numeric",
  month: "numeric",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  hourCycle: "h23",
});

interface IstFields {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: string;
}

function istFields(d: Date): IstFields {
  const p: Record<string, string> = {};
  for (const part of istFormatter.formatToParts(d)) p[part.type] = part.value;
  return { year: Number(p.year), month: Number(p.month), day: Number(p.day), hour: Number(p.hour) % 24, minute: p.minute };
}

/** Days since an arbitrary epoch, counted on IST calendar days. */
function istDayNumber(d: Date): number {
  const f = istFields(d);
  return Math.round(Date.UTC(f.year, f.month - 1, f.day) / 86_400_000);
}

function toDate(input: string | Date | number | null | undefined): Date | null {
  if (input === null || input === undefined || input === "") return null;
  const d = new Date(input);
  return Number.isNaN(d.getTime()) ? null : d;
}

function clock(f: IstFields): string {
  const h12 = f.hour % 12 || 12;
  return `${h12}:${f.minute} ${f.hour < 12 ? "am" : "pm"}`;
}

/** "10:50 am IST". */
export function formatIstTime(input: string | Date | number | null | undefined): string {
  const d = toDate(input);
  return d ? `${clock(istFields(d))} IST` : "";
}

/** "14 Oct 2026, 5:30 pm IST", or "14 Oct 2026" for a date-only item. */
export function formatFullDateTime(input: string | Date | number | null | undefined, dateOnly = false): string {
  const d = toDate(input);
  if (!d) return "";
  const f = istFields(d);
  const date = `${f.day} ${MONTHS[f.month - 1]} ${f.year}`;
  return dateOnly ? date : `${date}, ${clock(f)} IST`;
}

function shortDate(d: Date, now: number): string {
  const f = istFields(d);
  const sameYear = f.year === istFields(new Date(now)).year;
  return `${f.day} ${MONTHS[f.month - 1]}${sameYear ? "" : ` ${f.year}`}`;
}

/**
 * "Just now", "12m ago", "6h ago" (same IST day), "Yesterday", "3d ago", then "14 Oct".
 * A time in the future (clock skew, or a feed's wrong zone) is shown as itself, never as
 * "Just now". A date-only item reads "Today" / "Yesterday" / "3d ago" / "14 Oct".
 */
export function formatRelativeTime(
  input: string | Date | number | null | undefined,
  now: number = Date.now(),
  dateOnly = false
): string {
  const d = toDate(input);
  if (!d) return "";
  const diffMs = now - d.getTime();
  const dayGap = istDayNumber(new Date(now)) - istDayNumber(d);

  if (dateOnly) {
    if (dayGap < 0) return shortDate(d, now);
    if (dayGap === 0) return "Today";
    if (dayGap === 1) return "Yesterday";
    if (dayGap < 7) return `${dayGap}d ago`;
    return shortDate(d, now);
  }
  if (diffMs < 0) {
    const f = istFields(d);
    return `${f.day} ${MONTHS[f.month - 1]}, ${clock(f)}`;
  }
  if (diffMs < 60_000) return "Just now";
  if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)}m ago`;
  if (dayGap === 0) return `${Math.floor(diffMs / 3_600_000)}h ago`;
  if (dayGap === 1) return "Yesterday";
  if (dayGap < 7) return `${dayGap}d ago`;
  return shortDate(d, now);
}

export function getDomain(url: string | null | undefined): string {
  if (!url) return "";
  try {
    return new URL(url).hostname.replace(/^www\./i, "");
  } catch {
    return "";
  }
}

export function categoryLabel(slug: string | null | undefined): string {
  return NEWS_CATEGORIES.find((c) => c.slug === slug)?.label ?? "General";
}

export function regionLabel(slug: string | null | undefined): string {
  return (slug && NEWS_REGION_LABELS[slug as NewsRegionSlug]) || "";
}

export function sourceInitial(source: string | null | undefined): string {
  return ((source || "").trim().charAt(0) || "N").toUpperCase();
}

export interface SentimentStyle {
  label: string;
  dotColor: string;
  badgeBg: string;
  textColor: string;
}

export function getSentimentStyle(sentiment: string | null | undefined): SentimentStyle {
  const norm = (sentiment || "").toLowerCase().trim();
  if (norm === "positive") {
    return { label: "Positive", dotColor: "var(--mf-success)", badgeBg: "var(--mf-success-bg)", textColor: "var(--mf-success)" };
  }
  if (norm === "negative") {
    return { label: "Negative", dotColor: "var(--mf-danger)", badgeBg: "var(--mf-danger-bg)", textColor: "var(--mf-danger)" };
  }
  return { label: "Neutral", dotColor: "var(--mf-muted)", badgeBg: "rgba(100, 116, 139, 0.12)", textColor: "var(--mf-muted)" };
}

export function feedStateStyle(state: FeedState | string): { label: string; color: string } {
  switch (state) {
    case "ok":
      return { label: "OK", color: "var(--mf-success)" };
    case "stale":
      return { label: "Stale", color: "var(--mf-warning)" };
    case "failing":
      return { label: "Failing", color: "var(--mf-danger)" };
    case "backoff":
      return { label: "Failing (paused)", color: "var(--mf-danger)" };
    default:
      return { label: "Not checked yet", color: "var(--mf-muted)" };
  }
}

export function paywallNote(paywall: string | null | undefined): string {
  if (paywall === "partial") return "Some articles need a subscription";
  if (paywall === "soft") return "May ask you to subscribe after a few articles";
  return "";
}
