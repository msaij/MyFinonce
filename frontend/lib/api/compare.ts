import { apiGet } from "./client";

/** [ISO date, value] */
export type Point = [string, number | null];

export interface ComparePeriod {
  start: string;
  end: string;
  days: number;
  coverage_pct: number;
  /** Funds whose first NAV came after the window's start and so pushed the period's start later. */
  limited_start_by: number[];
  /** Funds that had not yet published the latest NAV the others had, and so set the end. */
  limited_end_by: number[];
  /** The newest NAV date any fund in the period has (how far behind the end-setter is). */
  latest_end?: string | null;
  fund_count: number;
}

export interface FundPeriodReturn {
  start_date: string;
  start_nav: number;
  end_date: string;
  end_nav: number;
  return_pct: number | null;
  days: number;
}

export interface CompareFund {
  scheme_code: number;
  /** "Name (Plan - Option) [AMFI code]" */
  display_name: string;
  scheme_name?: string;
  fund_house?: string | null;
  category?: string | null;
  sebi_category?: string | null;
  asset_class?: string | null;
  plan_type?: string | null;
  option_type?: string | null;
  isin?: string | null;
  is_idcw?: boolean;
  is_active?: boolean | null;
  expense_ratio?: number | null;
  ter_status?: string | null;
  ter_as_of_date?: string | null;
  latest_nav?: number | null;
  latest_date?: string | null;
  return_30d_pct?: number | null;
  return_90d_pct?: number | null;
  return_1y_pct?: number | null;
  return_3y_cagr_pct?: number | null;
  high_52w?: number | null;
  dist_from_52w_high_pct?: number | null;
  riskometer?: string | null;
  riskometer_as_of?: string | null;
  aum_cr?: number | null;
  aum_as_of?: string | null;
  official?: {
    benchmark: string | null;
    as_of: string | null;
    fund_name: string | null;
    fund_1y_pct: number | null;
    bench_1y_pct: number | null;
    excess_1y_pp: number | null;
    fund_3y_pct: number | null;
    bench_3y_pct: number | null;
    excess_3y_pp: number | null;
    fund_5y_pct: number | null;
    bench_5y_pct: number | null;
    excess_5y_pp: number | null;
  } | null;
  peer?: {
    peer_group: string;
    n_1y: number;
    median_1y_pct: number | null;
    rank_1y: number | null;
    n_3y: number;
    median_3y_cagr_pct: number | null;
    rank_3y: number | null;
    n_ter: number;
    median_ter_pct: number | null;
    rank_note: string | null;
  };
  own_window?: (FundPeriodReturn & { anchored_before_start: boolean }) | null;
  status: "ok" | "stale" | "no_data" | "not_found";
  status_note?: string | null;
  series?: Point[];
  common?: (FundPeriodReturn & { cagr_pct: number | null }) | null;
  risk?: {
    vol_ann_pct: number | null;
    obs_per_year: number;
    max_drawdown_pct: number | null;
    max_drawdown_peak_date: string | null;
    max_drawdown_trough_date: string | null;
    sharpe: number | null;
    sortino: number | null;
    n_returns: number;
    short_sample: boolean;
  } | null;
  cost?: {
    fee_pct_of_initial: number | null;
    avg_ter_pct: number | null;
    history_coverage_pct: number;
    basis: "history" | "mixed" | "current";
  } | null;
  rolling_1y?: {
    points: Point[];
    min: number | null;
    median: number | null;
    max: number | null;
    pct_positive: number | null;
    n: number;
  } | null;
}

export interface CompareResult {
  requested: { start: string; end: string; days: number };
  common: ComparePeriod | null;
  rf_pct: number;
  funds: CompareFund[];
  /** `basis`: "weekly" when the period holds at least `min_obs` weekly returns for every
   *  pair (funds priced on different market clocks do not move on the same date), else
   *  "daily". `n_obs` counts returns on that basis. */
  correlation: { codes: number[]; matrix: (number | null)[][]; n_obs: number[][]; min_obs: number; basis?: "weekly" | "daily" } | null;
  limits: { max_funds: number; anchor_max_gap_days: number; stale_after_days: number; short_sample_returns: number; rolling_days: number };
}

export const getCompare = (codes: number[], start?: string, end?: string, rfPct?: number) =>
  apiGet<CompareResult>("/api/schemes/compare", {
    codes: codes.join(","),
    start,
    end,
    rf_pct: rfPct,
  });
