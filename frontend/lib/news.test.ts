import { describe, expect, it } from "vitest";
import { NEWS_CATEGORIES, NEWS_REGION_LABELS } from "@/components/news/types";
import { buildNewsParams } from "./api/news";
import {
  categoryLabel,
  formatFullDateTime,
  formatIstTime,
  formatRelativeTime,
  getDomain,
  getSentimentStyle,
  regionLabel,
  sourceInitial,
} from "./newsFormat";
import * as newsFormat from "./newsFormat";

describe("category and region contract with the backend", () => {
  it("pins the slugs and labels news_service.CATEGORY_LABELS uses (backend/tests/test_news.py pins the same)", () => {
    expect(NEWS_CATEGORIES.filter((c) => c.slug !== "all").map((c) => [c.slug, c.label])).toEqual([
      ["central_banks", "Central Banks"],
      ["macro", "Macro"],
      ["equities", "Equities"],
      ["commodities_forex", "FX & Commodities"],
    ]);
    expect(Object.keys(NEWS_REGION_LABELS)).toEqual(["global", "india", "us", "uk", "europe", "asia"]);
  });

  it("sends the slug for every chip, never its label, and nothing for All", () => {
    for (const { slug, label } of NEWS_CATEGORIES) {
      const params = buildNewsParams({ category: slug });
      if (slug === "all") {
        expect(params.category).toBeUndefined();
      } else {
        expect(params.category).toBe(slug);
        expect(params.category).not.toBe(label);
      }
    }
    expect(buildNewsParams({ category: "central_banks", search: "  rbi  " })).toEqual({ category: "central_banks", search: "rbi" });
  });

  it("labels slugs for display", () => {
    expect(categoryLabel("commodities_forex")).toBe("FX & Commodities");
    expect(categoryLabel("central_banks")).toBe("Central Banks");
    expect(regionLabel("india")).toBe("India");
    expect(regionLabel("asia")).toBe("Asia-Pacific");
  });
});

describe("relative times, by IST calendar day", () => {
  const now = new Date("2026-10-14T12:00:00Z").getTime(); // 5:30 pm IST

  it("handles empty or invalid dates", () => {
    expect(formatRelativeTime(null, now)).toBe("");
    expect(formatRelativeTime(undefined, now)).toBe("");
    expect(formatRelativeTime("invalid-date", now)).toBe("");
  });

  it("says 'Just now' only for the last minute, never for a future time", () => {
    expect(formatRelativeTime(new Date("2026-10-14T11:59:45Z"), now)).toBe("Just now");
    const future = formatRelativeTime(new Date("2026-10-14T17:30:00Z"), now);
    expect(future).not.toBe("Just now");
    expect(future).toBe("14 Oct, 11:00 pm");
  });

  it("counts minutes, then hours within the same IST day", () => {
    expect(formatRelativeTime(new Date("2026-10-14T11:48:00Z"), now)).toBe("12m ago");
    expect(formatRelativeTime(new Date("2026-10-14T11:05:00Z"), now)).toBe("55m ago");
    expect(formatRelativeTime(new Date("2026-10-14T11:00:00Z"), now)).toBe("1h ago");
    expect(formatRelativeTime(new Date("2026-10-14T06:00:00Z"), now)).toBe("6h ago");
    // 20:00 UTC on the 13th is 1:30 am IST on the 14th: still today in IST.
    expect(formatRelativeTime(new Date("2026-10-13T20:00:00Z"), now)).toBe("16h ago");
  });

  it("uses calendar days for Yesterday and 'Nd ago', not 24-48 hour gaps", () => {
    expect(formatRelativeTime(new Date("2026-10-13T10:00:00Z"), now)).toBe("Yesterday");
    expect(formatRelativeTime(new Date("2026-10-12T15:00:00Z"), now)).toBe("2d ago");
    expect(formatRelativeTime(new Date("2026-10-11T12:00:00Z"), now)).toBe("3d ago");
  });

  it("shows older dates as a day and month, with the year when it differs", () => {
    expect(formatRelativeTime(new Date("2026-09-15T12:00:00Z"), now)).toBe("15 Sep");
    expect(formatRelativeTime(new Date("2025-09-15T12:00:00Z"), now)).toBe("15 Sep 2025");
  });

  it("shows date-only items as a day, never as hours ago", () => {
    expect(formatRelativeTime("2026-10-13T18:30:00Z", now, true)).toBe("Today"); // 14 Oct 00:00 IST
    expect(formatRelativeTime("2026-10-12T18:30:00Z", now, true)).toBe("Yesterday");
    expect(formatRelativeTime("2026-10-09T18:30:00Z", now, true)).toBe("4d ago");
  });
});

describe("absolute times in IST", () => {
  it("formats en-IN style in IST", () => {
    expect(formatFullDateTime("2026-10-14T12:00:00Z")).toBe("14 Oct 2026, 5:30 pm IST");
    expect(formatFullDateTime("2026-10-13T18:30:00Z", true)).toBe("14 Oct 2026");
    expect(formatIstTime("2026-10-15T05:20:00Z")).toBe("10:50 am IST");
    expect(formatFullDateTime("invalid")).toBe("");
    expect(formatFullDateTime(null)).toBe("");
  });
});

describe("news privacy and display helpers", () => {
  it("has no third-party favicon helper", () => {
    expect("getFaviconUrl" in newsFormat).toBe(false);
  });

  it("extracts a clean domain and a source initial", () => {
    expect(getDomain("https://www.bloomberg.com/news/articles")).toBe("bloomberg.com");
    expect(getDomain("invalid")).toBe("");
    expect(sourceInitial("Reserve Bank of India")).toBe("R");
    expect(sourceInitial("")).toBe("N");
  });

  it("resolves sentiment styles", () => {
    expect(getSentimentStyle("positive").dotColor).toBe("var(--mf-success)");
    expect(getSentimentStyle("negative").dotColor).toBe("var(--mf-danger)");
    expect(getSentimentStyle(null).label).toBe("Neutral");
    expect(getSentimentStyle("unknown").label).toBe("Neutral");
  });
});
