import { apiGet } from "./client";

export interface ScreenerRow {
  [key: string]: unknown;
  scheme_code: number;
  scheme_name: string;
  fund_house: string;
  category: string;
  broad_category: string;
  plan_type: string;
  option_type: string;
  expense_ratio: number | null;
  ter_status: string | null;
  ter_source: string | null;
  ter_as_of_date: string | null;
  exit_load_pct: number | null;
  exit_load_days: number | null;
  exit_load_description: string | null;
  exit_rule_status: string | null;
  exit_rule_source: string | null;
  exit_rule_as_of_date: string | null;
  lock_in_years: number | null;
  latest_nav: number | null;
  latest_date: string | null;
  change_1d_pct: number | null;
  return_7d_pct: number | null;
  return_30d_pct: number | null;
  return_90d_pct: number | null;
  return_1y_pct: number | null;
  period_return_pct: number | null;
  high_52w: number | null;
  low_52w: number | null;
  dist_from_52w_high_pct: number | null;
  isin: string | null;
}

export interface ScreenerKpis {
  total_schemes: number;
  latest_date: string | null;
  top_performer: { name: string; return_pct: number | null } | null;
  lag_performer: { name: string; return_pct: number | null } | null;
  advancers: number;
  decliners: number;
  unchanged: number;
  median_return: number;
  avg_return: number;
  best_cat: { name: string; return_pct: number | null } | null;
}

export interface ScreenerFilterParams {
  amc?: string;
  broad_cat?: string;
  sub_cat?: string;
  plan_type?: string;
  option_type?: string;
  search_term?: string;
  start?: string;
  end?: string;
  scheme_code?: number;
  max_expense_ratio?: number;
}

export const getScreener = (
  filters: ScreenerFilterParams,
  sortBy: string,
  ascending: boolean,
  limit: number
) =>
  apiGet<ScreenerRow[]>("/api/screener", {
    ...filters,
    sort_by: sortBy,
    ascending,
    limit,
  });

export const getScreenerKpis = (filters: ScreenerFilterParams) => apiGet<ScreenerKpis>("/api/screener/kpis", { ...filters });
