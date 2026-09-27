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
  /** How often the fund prices a NAV a year; volatility is annualised with it. */
  obs_per_year: number | null;
  n_trading_days: number;
  win_rate_pct: number | null;
  /** Worst fall from a running peak inside the window, % (negative). */
  max_drawdown_pct: number | null;
  /** The asset class the money is in (Equity, Debt, Cash & Liquid, ...). */
  asset_class: string;
  /** SEBI category, AMFI's legacy label folded in. */
  peer_category: string;
  /** Same asset class, category and plan, priced across the window. */
  peer_count: number;
  /** 1 = best return among its peers. */
  peer_rank: number;
  /** Share of peers it beat: 100 = best. Null under 5 peers. */
  peer_percentile: number | null;
  /** Null when there are under 5 peers to take a median of. */
  cat_median_return: number | null;
  /** Return minus the peer median, in percentage points. Null under 5 peers. */
  cat_alpha_pct: number | null;
  quartile: number | null;
  quartile_rank: string;
  /** Median annualised volatility of its peers; null under 5 peers. */
  peer_median_vol: number | null;
  /** Its volatility against that median, %: -20 = 20% calmer than its peers. */
  vol_vs_peers_pct: number | null;
  /** Return over the window's last third. */
  recent_return_pct: number | null;
  /** Peer-relative: "Ahead of peers, calmer" etc. Absent without a peer position. */
  quadrant?: string | null;
  diagnostic_classification: string;
  return_to_risk: number | null;
  /** AMFI's figures for the fund's SEBI benchmark (Growth plans matched to AMFI's feed). */
  benchmark?: string | null;
  official_1y_pct?: number | null;
  benchmark_1y_pct?: number | null;
  excess_1y_pp?: number | null;
  official_3y_pct?: number | null;
  benchmark_3y_pct?: number | null;
  excess_3y_pp?: number | null;
}

export interface NamedReturn {
  name: string;
  return_pct: number;
  funds?: number;
  category?: string;
}

export interface LeadersResponse {
  rows: LeaderRow[];
  excluded_thin_data: number;
  /** Launched or stopped pricing inside the window, so their return covers a shorter period. */
  excluded_partial_window: number;
  /** IDCW plans: a payout lowers the NAV and would read as a loss. */
  excluded_idcw: number;
  window: { first_nav_date?: string; last_nav_date?: string; vol_start?: string };
  asset_classes: string[];
  categories_by_class: Record<string, string[]>;
  total_funds: number;
  advancers: number;
  decliners: number;
  market_median_return: number | null;
  top_alpha: NamedReturn | null;
  leading_category: NamedReturn | null;
  lagging_category: NamedReturn | null;
  med_vol: number | null;
  med_ret: number | null;
  quadrant_excluded: number;
  vol_lookback?: string;
  vol_methodology?: string;
}

export type RotationQuadrant = "Leading" | "Weakening" | "Lagging" | "Improving";

export interface RotationCategory {
  [key: string]: unknown;
  asset_class: string;
  category: string;
  /** Distinct funds (a fund's Direct and Regular plans count once). */
  funds: number;
  aum_cr: number | null;
  median_return: number;
  /** Median return over the window's last `recent_days`. */
  recent_median_return: number | null;
  /** Median return minus the market median over the window, pp. */
  strength_pp: number;
  /** The same over the recent stretch, pp. */
  momentum_pp: number | null;
  quadrant: RotationQuadrant | null;
  p25_return: number | null;
  p75_return: number | null;
  pct_up: number | null;
  median_vol: number | null;
  median_drawdown: number | null;
  avg_ter: number | null;
  /** Median fund return in each period of `periods`. */
  heat: (number | null)[];
}

export interface RotationResponse {
  categories: RotationCategory[];
  /** Period start dates (ISO). */
  periods: string[];
  market_period_median: (number | null)[];
  unit: "week" | "month" | null;
  recent_days: number | null;
  market_median: number | null;
  market_recent_median: number | null;
}

export const getRotation = (params: { start: string; end: string; broad_cat?: string; plan_type?: string; option_type?: string }) =>
  apiGet<RotationResponse>("/api/leaders/rotation", params);

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
