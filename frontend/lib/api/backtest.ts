import { apiPost } from "./client";

export type BacktestMode = "Lump Sum" | "SIP (Monthly)";
export type RebalanceFreq = "None" | "Monthly" | "Quarterly" | "Annually";
export type BenchmarkKind = "none" | "category" | "scheme";

export interface BacktestRequest {
  scheme_codes: number[];
  weights: Record<number, number>;
  mode: BacktestMode;
  lump_sum_amount: number;
  sip_amount: number;
  rebalance_freq: RebalanceFreq;
  start_date: string;
  end_date: string;
  /** 1-31 (clamped to month end); null = the day of the window's start date. */
  sip_day?: number | null;
  apply_stamp_duty?: boolean;
  exit_load_pct?: number;
  benchmark?: BenchmarkKind;
  benchmark_code?: number | null;
}

export interface BacktestResultRow {
  [key: string]: unknown;
  nav_date: string;
  portfolio_value: number;
  total_invested: number;
  /** Chain-linked time-weighted index of the simulated portfolio, 100 before day one. */
  twr_index: number;
  /** Drawdown of that index from its running peak, in %. */
  drawdown_pct: number;
  benchmark_value?: number | null;
  benchmark_index?: number | null;
  benchmark_drawdown_pct?: number | null;
}

export interface TwrMetrics {
  cagr_pct: number | null;
  total_return_pct?: number | null;
  sharpe_ratio: number | null;
  sortino_ratio?: number | null;
  max_drawdown_pct: number | null;
  vol_annualized_pct: number | null;
  obs_per_year?: number;
  annualise_withheld?: boolean;
  span_days?: number;
  risk_free_rate_pct?: number;
  [key: string]: unknown;
}

export interface BacktestFund {
  scheme_code: number;
  display_name: string;
  category: string | null;
  option_type: string | null;
  expense_ratio: number | null;
  ter_status: string | null;
  is_idcw: boolean;
  growth_alternative: { scheme_code: number; display_name: string } | null;
  first_nav_date: string | null;
  last_nav_date: string | null;
  target_weight_pct: number;
  final_weight_pct: number | null;
  contributed: number;
  rebalance_bought: number;
  rebalance_sold: number;
  net_invested: number;
  final_value: number;
  units: number;
  gain: number;
  gain_share_pct: number | null;
  xirr_pct: number | null;
  xirr_note: string | null;
  nav_start: number;
  nav_end: number;
  nav_return_pct: number;
}

export interface BacktestTrade {
  date: string;
  type: "SIP" | "Lump sum" | "Rebalance sell" | "Rebalance buy";
  scheme_code: number;
  display_name: string;
  amount: number;
  nav: number;
  units: number;
  stamp_duty: number;
  exit_load: number;
}

export interface BacktestCalendarYear {
  year: number;
  from: string;
  to: string;
  partial: boolean;
  portfolio_pct: number;
  benchmark_pct: number | null;
  funds: Record<string, number | null>;
}

export interface BacktestBenchmark {
  kind: BenchmarkKind;
  name?: string;
  error?: string;
  scheme_code?: number;
  is_idcw?: boolean;
  /** asset_class: the peer group inside the category; own_nav_reason: why the fund's own NAV stood in. */
  components?: { scheme_code: number; category: string | null; asset_class?: string | null; weight_pct: number; own_nav_used: boolean; own_nav_reason?: string | null }[];
  final_value?: number;
  total_invested?: number;
  absolute_gain?: number;
  money_weighted_xirr_pct?: number | null;
  xirr_note?: string | null;
  twr_cagr_pct?: number | null;
  twr_total_return_pct?: number | null;
  vol_annualized_pct?: number | null;
  max_drawdown_pct?: number | null;
  sharpe_ratio?: number | null;
  first_date?: string;
}

export interface BacktestWarning {
  code: string;
  level: "info" | "warning";
  message: string;
}

export interface BacktestResult {
  error?: string;
  missing_codes?: number[];
  df_result?: BacktestResultRow[];
  twr_metrics?: TwrMetrics;
  final_value?: number;
  total_invested?: number;
  n_contributions?: number;
  absolute_gain?: number;
  absolute_return_pct?: number | null;
  money_weighted_xirr_pct?: number | null;
  xirr_note?: string | null;
  normalized_weights?: Record<number, number>;
  zero_weight_codes?: number[];
  actual_start?: string;
  actual_end?: string;
  funds?: BacktestFund[];
  trades?: BacktestTrade[];
  calendar_years?: BacktestCalendarYear[];
  benchmark?: BacktestBenchmark;
  costs?: {
    stamp_duty: number;
    exit_load: number;
    rebalance_count: number;
    rebalance_turnover: number;
    rebalance_sold_under_exit_load_days: number;
  };
  window?: {
    requested_start: string;
    requested_end: string;
    data_start: string;
    data_end: string;
    simulated_start: string;
    simulated_end: string;
    span_days: number;
    start_limited_by: number[];
    end_limited_by: number[];
  };
  assumptions?: {
    mode: BacktestMode;
    sip_day: number | null;
    sip_day_defaulted: boolean;
    stamp_duty_applied: boolean;
    stamp_duty_rate_pct: number;
    stamp_duty_from: string;
    exit_load_pct: number;
    exit_load_days: number;
    risk_free_rate_pct: number;
    obs_per_year: number;
    min_annualise_days: number;
    rebalance_freq: RebalanceFreq;
    tax_modelled: boolean;
  };
  warnings?: BacktestWarning[];
}

export const runBacktest = (req: BacktestRequest) => apiPost<BacktestResult>("/api/backtest", req);
