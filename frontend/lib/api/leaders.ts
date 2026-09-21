import { apiGet } from "./client";

export interface LeaderRow {
  [key: string]: unknown;
  scheme_code: number;
  scheme_name: string;
  display_name: string;
  fund_house: string;
  category: string;
  broad_category: string;
  plan_type: string;
  option_type: string;
  expense_ratio: number | null;
  latest_nav: number | null;
  dist_from_52w_high_pct: number | null;
  period_return_pct: number;
  annualized_vol_pct: number | null;
  n_trading_days: number;
  win_rate_pct: number | null;
  cat_median_return: number;
  cat_alpha_pct: number;
  quartile: number;
  quartile_rank: string;
  quadrant?: string;
  diagnostic_classification: string;
  return_to_risk: number | null;
}

export interface NamedReturn {
  name: string;
  return_pct: number;
}

export interface LeadersResponse {
  rows: LeaderRow[];
  excluded_thin_data: number;
  total_funds: number;
  advancers: number;
  decliners: number;
  market_median_return: number | null;
  top_alpha: NamedReturn | null;
  leading_category: NamedReturn | null;
  med_vol: number | null;
  med_ret: number | null;
  quadrant_excluded: number;
  vol_lookback?: string;
  vol_methodology?: string;
}

export const getLeaders = (params: {
  broad_cat?: string;
  sub_cat?: string;
  plan_type?: string;
  option_type?: string;
  search?: string;
  start?: string;
  end?: string;
  vol_lookback?: string;
}) => apiGet<LeadersResponse>("/api/leaders", params);
