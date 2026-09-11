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

export interface SchemeProfile {
  [key: string]: unknown;
  scheme_code: number;
  scheme_name: string;
  fund_house: string;
  category: string;
  plan_type: string;
  option_type?: string;
  expense_ratio: number | null;
  ter_status: string | null;
  ter_base_expense_ratio: number | null;
  ter_brokerage_cost_pct: number | null;
  ter_transaction_cost_pct: number | null;
  ter_statutory_levies_pct: number | null;
  exit_load_description: string | null;
  exit_rule_status: string | null;
  lock_in_years: number | null;
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
