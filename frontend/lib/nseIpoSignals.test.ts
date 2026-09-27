import { describe, expect, it } from "vitest";

import type { NseBidRow, NseIssueDetail } from "@/lib/api/nseIpo";
import { biddingDays, composition, firstInt, ipoSignals, stageOf, topOfBandShare, upperPrice } from "./nseIpoSignals";

const row = (sr_no: string | null, category: string, shares_offered: number | null, subscription_times: number | null): NseBidRow => ({
  sr_no,
  category,
  shares_offered,
  shares_bid: null,
  subscription_times,
});

const ORIENT_SIZE =
  "Initial Public offer comprising of Fresh issue aggregating up to Rs. 3,200 million and Offer for Sale aggregating up to Rs. 2,320 million (including Anchor investor portion of 60,88,233 Equity shares)";

function detail(rows: NseBidRow[], size = ORIENT_SIZE, extra: Partial<NseIssueDetail> = {}): NseIssueDetail {
  return {
    symbol: "X",
    series: "EQ",
    heading: null,
    notices: [],
    issue_info: [
      { title: "Price Range", value: "Rs. 258 to Rs. 272 per Equity Share", url: null },
      { title: "Discount", value: "NA", url: null },
      { title: "Bid Lot", value: "55 Equity Shares and in multiples thereof", url: null },
      { title: "Issue Size", value: size, url: null },
    ],
    listing: null,
    bid_details_nse: [],
    bid_details_consolidated: rows,
    consolidated_updated: "Updated as on 25-Sep-2026 17:00:00",
    demand_graph_nse: null,
    demand_graph_all: null,
    demand_data_nse: [],
    demand_data_all: [],
    ...extra,
  };
}

const tile = (r: ReturnType<typeof ipoSignals>, label: string) => r.tiles.find((t) => t.label === label);

describe("parsers", () => {
  it("reads the upper end of a price band in NSE's spellings", () => {
    expect(upperPrice("Rs. 258 to Rs. 272 per Equity Share")).toBe(272);
    expect(upperPrice("Rs. 123/- to Rs. 130/-per equity share")).toBe(130);
    expect(upperPrice("Rs.1,104 to Rs.1,160")).toBe(1160);
  });
  it("reads the lot size", () => {
    expect(firstInt("55 Equity Shares and in multiples thereof")).toBe(55);
    expect(firstInt("1,200 Equity Shares")).toBe(1200);
  });
});

describe("composition (real NSE Issue Size lines)", () => {
  it("splits money-denominated parts and finds the anchor book", () => {
    const c = composition(ORIENT_SIZE, 272);
    expect(c.fresh).toBe(3200e6);
    expect(c.ofs).toBe(2320e6);
    expect(c.anchorShares).toBe(6088233);
  });
  it("finds the fresh part even when NSE leaves out the words 'fresh issue'", () => {
    const c = composition(
      "Initial public offering comprising of aggregating up to 7500 million and offer for sale up to 10,04,94,200 Equity Shares (including Anchor portion of 9,63,24,729 Equity Shares)",
      34
    );
    expect(c.fresh).toBe(7500e6);
    expect(c.ofs).toBe(100494200 * 34);
  });
  it("mixes lakhs, share counts and bare numbers", () => {
    expect(composition("Fresh issue aggregating up to Rs. 29,000 lakhs and Offer for Sale of up to 10,00,000 Equity Shares", 139)).toMatchObject({ fresh: 29000e5, ofs: 1000000 * 139 });
    expect(composition("Fresh Issue up to 58,90,800 Equity Shares and Offer for Sale up to 7,14,000 (Including market maker portion)", 103)).toMatchObject({
      fresh: 5890800 * 103,
      ofs: 714000 * 103,
    });
  });
  it("ignores amounts inside the parenthetical (employee and anchor portions)", () => {
    const c = composition("Fresh Issue aggregating up to Rs. 5,000 million (including Employee reservation portion aggregating up to Rs. 35 million and Anchor portion of 48,83,605 Equity Shares)", 305);
    expect(c).toMatchObject({ fresh: 5000e6, ofs: null, anchorShares: 4883605, employeeQuota: true });
  });
});

describe("time", () => {
  it("counts weekdays only", () => {
    expect(biddingDays("2026-09-26", "2026-09-29")).toBe(2); // Sat -> Tue: Mon, Tue
    expect(biddingDays("2026-09-25", "2026-09-29")).toBe(3); // Fri, Mon, Tue
  });
  it("classifies by status and dates; the last day is only the closing date itself", () => {
    const today = "2026-09-26"; // a Saturday
    expect(stageOf({ status: "Forthcoming", issue_start: "2026-09-28", issue_end: "2026-09-30", series: "EQ" }, today)).toBe("upcoming");
    expect(stageOf({ status: "Active", issue_start: "2026-09-24", issue_end: "2026-09-28", series: "EQ" }, today)).toBe("open");
    expect(stageOf({ status: "Active", issue_start: "2026-09-24", issue_end: "2026-09-26", series: "EQ" }, today)).toBe("last-day");
    expect(stageOf({ status: "Closed", issue_start: "2026-09-23", issue_end: "2026-09-25", series: "EQ" }, today)).toBe("closed");
  });
});

describe("ipoSignals", () => {
  const lastDay = { status: "Active", issue_start: "2026-09-24", issue_end: "2026-09-28", series: "EQ", issue_size: 14976743 };
  const open = { status: "Active", issue_start: "2026-09-25", issue_end: "2026-09-29", series: "EQ" };
  const strong = [
    row("1", "Qualified Institutional Buyers(QIBs)", 100, 45),
    row("2", "Non Institutional Investors", 100, 30),
    row("3", "Retail Individual Investors(RIIs)", 100, 8),
    row(null, "Total", 300, 28),
  ];

  it("calls a heavily subscribed, institution-backed issue worth a shot", () => {
    const r = ipoSignals(detail(strong), lastDay, "2026-09-28");
    expect(r.verdict.label).toBe("Worth a shot");
    expect(r.verdict.asOf).toBe("Updated as on 25-Sep-2026 17:00:00");
    expect(tile(r, "Timing")?.value).toBe("Last day");
    expect(tile(r, "Retail")?.sub).toBe("Allotment odds about 1 in 8 or better");
    expect(tile(r, "Minimum application")?.value).toBe("Rs 14,960");
    expect(tile(r, "Where the money goes")?.value).toBe("58% fresh · 42% OFS");
    expect(tile(r, "Issue size")?.value).toBe("Rs 552 cr");
    expect(tile(r, "Issue size")?.sub).toBe("incl. Rs 166 cr anchor book");
    expect(tile(r, "Special quotas")).toBeUndefined();
  });

  it("reports final demand, not a recommendation, once bidding has closed", () => {
    const closed = { ...lastDay, status: "Closed" };
    expect(ipoSignals(detail(strong), closed, "2026-09-29").verdict.label).toBe("Closed: demand was strong");
    const weak = [row("1", "Qualified Institutional Buyers(QIBs)", 100, 0.4), row("2", "Non Institutional Investors", 100, 0.6), row(null, "Total", 300, 0.7)];
    expect(ipoSignals(detail(weak), closed, "2026-09-29").verdict.label).toBe("Closed: demand was weak");
  });

  it("says skip on the last day when the issue is not even covered", () => {
    const weak = [row("1", "Qualified Institutional Buyers(QIBs)", 100, 0.4), row("2", "Non Institutional Investors", 100, 0.6), row(null, "Total", 300, 0.7)];
    expect(ipoSignals(detail(weak), lastDay, "2026-09-28").verdict.label).toBe("Consider skipping");
  });

  it("does not punish low numbers while bidding is still open", () => {
    const r = ipoSignals(detail([row("1", "Qualified Institutional Buyers(QIBs)", 100, 0.01), row(null, "Total", 300, 0.3)]), open, "2026-09-26");
    expect(r.verdict.label).toBe("Too early to tell");
    expect(r.verdict.reasons.filter((x) => /last day/.test(x))).toEqual(["Institutions and HNIs mostly bid on the last day; early numbers understate demand"]);
    expect(tile(r, "Institutions (QIB)")?.tone).toBe("neutral");
    expect(tile(r, "Timing")?.value).toBe("2 bidding days left");
  });

  it("treats zero reserved quantity as no data, not zero demand (SME bid on BSE)", () => {
    const sme = { ...open, series: "SME", subscription_times: 0.24 };
    const r = ipoSignals(detail([row("1", "Qualified Institutional Buyers(QIBs)", 0, 0), row(null, "Total", 0, 0)]), sme, "2026-09-26");
    expect(tile(r, "Institutions (QIB)")?.value).toBe("-");
    const smeRetail = ipoSignals(detail([row("3", "Individual Investors", 1000, 2.5), row(null, "Total", 1000, 2.5)]), sme, "2026-09-26");
    expect(tile(smeRetail, "Retail")?.value).toBe("2.50x");
    expect(tile(smeRetail, "Special quotas")).toBeUndefined();
    expect(r.verdict.reasons.some((x) => x.startsWith("Overall 0.24x"))).toBe(true);
    expect(r.verdict.reasons.some((x) => x.startsWith("SME issue"))).toBe(true);
  });

  it("shows employee quotas and their discount when the issue has them", () => {
    const rows = [...strong.slice(0, 3), row("4", "Employees", 1000, 1.2), strong[3]];
    const d = detail(rows, "Fresh Issue aggregating up to Rs. 5,000 million (including Employee reservation portion)");
    d.issue_info = d.issue_info.map((i) => (i.title === "Discount" ? { ...i, value: "Discount of Rs. 14 per equity share is being offered to Eligible Employees" } : i));
    const q = tile(ipoSignals(d, lastDay, "2026-09-28"), "Special quotas");
    expect(q?.value).toBe("Employees 1.20x");
    expect(q?.sub).toMatch(/Rs\. 14/);
  });

  it("flags a pure offer for sale", () => {
    const r = ipoSignals(detail(strong, "Offer for Sale of up to 1,00,00,000 Equity Shares"), lastDay, "2026-09-28");
    expect(tile(r, "Where the money goes")?.value).toBe("100% OFS");
    expect(r.verdict.reasons.some((x) => x.startsWith("Pure offer for sale"))).toBe(true);
  });

  it("has no verdict before bidding opens", () => {
    const r = ipoSignals(detail([row(null, "Total", 0, 0)]), { status: "Forthcoming", issue_start: "2026-09-28", issue_end: "2026-09-30", series: "EQ" }, "2026-09-26");
    expect(r.verdict.label).toBe("Too early to tell");
    expect(tile(r, "Timing")?.value).toBe("Opens Mon 28 Sept");
  });
});

describe("topOfBandShare", () => {
  it("counts cut-off and the highest price as top of band", () => {
    const g = {
      heading: "", subheading: "", timestamp: null, total_bids: null, total_issue_size: null, bids_at_cutoff: null, times_subscribed: null, note: "", graph_logic_url: null,
      points: [
        { price: "258", qty_lakh: 10, cumulative_qty: null },
        { price: "272", qty_lakh: 60, cumulative_qty: null },
        { price: "Cut-off", qty_lakh: 30, cumulative_qty: null },
      ],
    };
    expect(topOfBandShare(g)).toBeCloseTo(0.9);
  });
});
