import { apiGet } from "./client";

/** Live NSE public-issue data, fetched by the backend from nseindia.com (app/nse_ipo_client.py).
 *  None of it is stored: the backend keeps no copy, and the page's queries use NSE_NO_RETAIN
 *  so the browser drops each result once nothing is showing it. */

export const NSE_NO_RETAIN = { gcTime: 0, staleTime: 0 } as const;

export interface NseCurrentIssue {
  [key: string]: unknown;
  company: string;
  symbol: string;
  series: string;
  issue_start: string | null;
  issue_end: string | null;
  status: string | null;
  price_range: string | null;
  issue_size: number | null;
  shares_offered: number | null;
  shares_bid: number | null;
  subscription_times: number | null;
}

export interface NseUpcomingIssue {
  [key: string]: unknown;
  company: string;
  symbol: string;
  series: string;
  issue_start: string | null;
  issue_end: string | null;
  status: string | null;
  price_range: string | null;
  issue_size: number | null;
}

export interface NseInfoItem {
  title: string | null;
  value: string;
  url: string | null;
}

export interface NseBidRow {
  [key: string]: unknown;
  sr_no: string | null;
  category: string;
  shares_offered: number | null;
  shares_bid: number | null;
  subscription_times: number | null;
}

export interface NseDemandGraph {
  heading: string;
  subheading: string;
  timestamp: string | null;
  total_bids: number | null;
  total_issue_size: number | null;
  bids_at_cutoff: number | null;
  times_subscribed: number | null;
  note: string;
  graph_logic_url: string | null;
  points: { price: string; qty_lakh: number | null; cumulative_qty: number | null }[];
}

export interface NseDemandRow {
  [key: string]: unknown;
  price: string;
  cumulative_qty: number | null;
  timestamp: string | null;
}

export interface NseIssueDetail {
  symbol: string;
  series: string | null;
  heading: string | null;
  notices: string[];
  issue_info: NseInfoItem[];
  listing: { isin: string | null; industry: string | null; listing_date: string | null } | null;
  bid_details_nse: NseBidRow[];
  bid_details_consolidated: NseBidRow[];
  consolidated_updated: string | null;
  demand_graph_nse: NseDemandGraph | null;
  demand_graph_all: NseDemandGraph | null;
  demand_data_nse: NseDemandRow[];
  demand_data_all: NseDemandRow[];
}

export const getNseCurrentIssues = () => apiGet<NseCurrentIssue[]>("/api/nse-ipo/current");
export const getNseUpcomingIssues = () => apiGet<NseUpcomingIssue[]>("/api/nse-ipo/upcoming");
export const getNseIssueDetail = (symbol: string, series?: string | null) =>
  apiGet<NseIssueDetail>("/api/nse-ipo/detail", { symbol, series: series ?? undefined });
