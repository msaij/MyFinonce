import { apiGet } from "./client";

export interface NamedReturn {
  name: string;
  return_pct: number | null;
}

/** One asset class over Growth-type plans: mean (avg_), median (med_) and sample size (n_)
 *  per trailing horizon, plus AMFI's reported assets. */
export interface AssetDistRow {
  [key: string]: unknown;
  asset_class: string;
  /** Growth-type plans whose returns are counted. */
  count: number;
  /** Every live scheme in the class, IDCW included. */
  schemes_all: number;
  avg_1d: number | null;
  avg_7d: number | null;
  avg_30d: number | null;
  avg_90d: number | null;
  avg_1y: number | null;
  med_1d: number | null;
  med_7d: number | null;
  med_30d: number | null;
  med_90d: number | null;
  med_1y: number | null;
  n_1y: number;
  aum_cr: number | null;
  aum_share_pct: number | null;
}

export interface AmcAumRow {
  [key: string]: unknown;
  fund_house: string;
  aum_cr: number;
  share_pct: number;
  funds: number;
}

export interface AmcScoreRow {
  [key: string]: unknown;
  fund_house: string;
  /** Schemes with a 1Y return and at least 5 same-category, same-plan peers. */
  ranked_schemes: number;
  /** Median of (scheme 1Y return - its peer median), in percentage points. */
  median_alpha_1y: number;
  beat_peers_pct: number;
  median_alpha_90d: number | null;
  schemes_count: number;
  aum_cr: number | null;
}

export interface OverviewStats {
  /** Every scheme ever listed, matured ones included. */
  total_schemes: number;
  /** Those still publishing NAVs -- what the page's counts mean. */
  active_schemes: number;
  total_amcs: number;
  total_nav_records: number;
  min_date: string | null;
  max_date: string | null;
  returns_pool: number;
  idcw_excluded: number;
  asset_classes: string[];
  asset_dist: AssetDistRow[];
  amc_aum: AmcAumRow[];
  amc_scorecard: AmcScoreRow[];
  aum_total_cr: number | null;
  aum_as_of: string | null;
  best_cat: NamedReturn | null;
}

export interface CategoryLeader {
  name: string;
  return_pct: number;
  funds?: number;
  category?: string;
}

/** Headline tiles for the date window, over the fair ranking pool (whole window, no IDCW). */
export interface PulseKpis {
  ranked_funds: number;
  advancers: number;
  decliners: number;
  unchanged: number;
  median_return: number | null;
  mean_return: number | null;
  top_alpha: CategoryLeader | null;
  leading_category: CategoryLeader | null;
  lagging_category: CategoryLeader | null;
  excluded_partial_window: number;
  excluded_idcw: number;
  first_nav_date: string | null;
  last_nav_date: string | null;
}

export interface MacroTrendPoint {
  nav_date: string;
  "Asset Class": string;
  "Indexed Performance": number;
  /** Funds in the basket that priced that day. */
  Funds: number;
}

export interface CategoryMatrixRow {
  [key: string]: unknown;
  "Asset Class": string;
  Category: string;
  Schemes: number;
  "AUM (Rs cr)": number | null;
  "Avg TER %": number | null;
  "Median 1D %": number | null;
  "Median 7D %": number | null;
  "Median 30D %": number | null;
  "Median 90D %": number | null;
  "Median 1Y %": number | null;
  "Funds with 1Y": number;
  "Median 52W High Gap %": number | null;
  "Top Fund (30D) %": number | null;
  "Top Fund (30D)": string | null;
  "Bottom Fund (30D) %": number | null;
  "Bottom Fund (30D)": string | null;
}

/** One scheme in the All Funds tab. Returns are trailing and cumulative (3Y and 5Y are not
 *  annualised), and are null for a scheme that has stopped publishing NAVs. */
export interface FundListRow {
  [key: string]: unknown;
  scheme_code: number;
  scheme_name: string;
  /** "Name (Plan - Option) [AMFI code]" -- the app-wide label. */
  display_name: string;
  fund_house: string | null;
  broad_category: string | null;
  category: string | null;
  plan_type: string | null;
  option_type: string | null;
  is_active: boolean | null;
  expense_ratio: number | null;
  latest_nav: number | null;
  latest_date: string | null;
  change_1d_pct: number | null;
  return_30d_pct: number | null;
  return_1y_pct: number | null;
  return_3y_pct: number | null;
  return_5y_pct: number | null;
  /** SEBI riskometer from AMFI's fund-performance data; null where AMFI publishes none. */
  riskometer: string | null;
  riskometer_as_of: string | null;
}

export const getAllFunds = (planType = "All Plans", optionType = "All Options") =>
  apiGet<FundListRow[]>("/api/overview/all-funds", { plan_type: planType, option_type: optionType });

export const getOverviewStats =(planType = "All Plans", optionType = "All Options") =>
  apiGet<OverviewStats>("/api/overview/stats", { plan_type: planType, option_type: optionType });

export const getPulseKpis = (startDate: string, endDate: string, planType: string, optionType: string = "All Options") =>
  apiGet<PulseKpis>("/api/overview/pulse-kpis", {
    start_date: startDate,
    end_date: endDate,
    plan_type: planType,
    option_type: optionType,
  });

export const getMacroTrend =(startDate: string, endDate: string, planType: string, optionType: string = "All Options") =>
  apiGet<MacroTrendPoint[]>("/api/overview/macro-trend", {
    start_date: startDate,
    end_date: endDate,
    plan_type: planType,
    option_type: optionType,
  });

export const getCategoryMatrix = (broadCategory: string, planType = "All Plans", optionType = "All Options") =>
  apiGet<CategoryMatrixRow[]>("/api/overview/category-matrix", {
    broad_category: broadCategory,
    plan_type: planType,
    option_type: optionType,
  });
