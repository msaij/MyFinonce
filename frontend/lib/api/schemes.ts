import { apiGet } from "./client";

export interface NavHistoryPoint {
  [key: string]: unknown;
  scheme_code: number;
  raw_scheme_name: string;
  base_scheme_name: string;
  scheme_name: string;
  plan_type: string | null;
  option_type: string | null;
  nav_date: string;
  nav: number;
}

export const getNavHistory = (codes: number[], start?: string, end?: string) =>
  apiGet<NavHistoryPoint[]>("/api/schemes/nav-history", {
    codes: codes.join(","),
    start,
    end,
  });

/** One scheme's percentile among ACTIVE funds of the same category and plan type. */
export interface PeerRank {
  value: number;
  percentile: number;
  peers: number;
}

export interface SchemeProfile {
  [key: string]: unknown;
  scheme_code: number;
  scheme_name: string;
  /** "Name (Plan - Option) [AMFI code]" -- the app-wide label. */
  display_name: string;
  fund_house: string;
  category: string;
  broad_category: string | null;
  plan_type: string;
  option_type?: string;
  is_active: boolean | null;
  expense_ratio: number | null;
  ter_status: string | null;
  ter_as_of_date: string | null;
  ter_source: string | null;
  ter_source_url: string | null;
  return_3y_pct: number | null;
  return_5y_pct: number | null;
  return_10y_pct: number | null;
  peer_rank?: Partial<Record<"1y" | "3y" | "5y", PeerRank | null>>;
  ter_base_expense_ratio: number | null;
  ter_brokerage_cost_pct: number | null;
  ter_transaction_cost_pct: number | null;
  ter_statutory_levies_pct: number | null;
  latest_nav: number | null;
  latest_date: string | null;
  change_1d_pct: number | null;
  return_7d_pct: number | null;
  return_30d_pct: number | null;
  return_90d_pct: number | null;
  return_1y_pct: number | null;
  high_52w: number | null;
  low_52w: number | null;
  dist_from_52w_high_pct: number | null;
  isin: string | null;
}

export interface SchemeDetail {
  profile: SchemeProfile;
  nav_history: NavHistoryPoint[];
}

export const getScheme = (code: number) => apiGet<SchemeDetail>(`/api/schemes/${code}`);

export const getSchemeProfile = (code: number) => apiGet<SchemeProfile>(`/api/schemes/${code}/profile`);

export const getSchemeTerHistory = (code: number) =>
  apiGet<{ ter_date: string; valid_to: string | null; total_ter_pct: number | null; base_expense_ratio_pct: number | null }[]>(
    `/api/schemes/${code}/ter-history`
  );
