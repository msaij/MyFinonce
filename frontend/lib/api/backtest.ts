import { apiPost } from "./client";

export interface BacktestRequest {
  scheme_codes: number[];
  weights: Record<number, number>;
  mode: "Lump Sum" | "SIP (Monthly)";
  lump_sum_amount: number;
  sip_amount: number;
  rebalance_freq: "None" | "Monthly" | "Quarterly" | "Annually";
  start_date: string;
  end_date: string;
}

export interface BacktestResultRow {
  [key: string]: unknown;
  nav_date: string;
  portfolio_value: number;
  total_invested: number;
}

export interface TwrMetrics {
  cagr_pct: number | null;
  sharpe_ratio: number | null;
  max_drawdown_pct: number | null;
  vol_annualized_pct: number | null;
  [key: string]: unknown;
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
  normalized_weights?: Record<number, number>;
  actual_start?: string;
}

export const runBacktest = (req: BacktestRequest) => apiPost<BacktestResult>("/api/backtest", req);
