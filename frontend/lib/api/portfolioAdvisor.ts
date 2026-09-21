import { apiGet, apiPost } from "./client";

export interface QuestionnaireQuestion {
  key: string;
  question: string;
  options: [string, number][];
}

export interface QuestionnaireData {
  questions: QuestionnaireQuestion[];
  risk_tiers: string[];
  sleeve_allocations: Record<string, Record<string, number>>;
  score_weights: Record<string, number>;
}

export const getQuestionnaire = () => apiGet<QuestionnaireData>("/api/portfolio-advisor/questionnaire");

export interface ScoreResult {
  score: number;
  risk_tier: string;
  suitability_warning?: string | null;
}

export const scoreQuestionnaire = (answers: Record<string, number>, horizonYears?: number) =>
  apiPost<ScoreResult>("/api/portfolio-advisor/score", { answers, horizon_years: horizonYears });

export interface PortfolioPick {
  sleeve: string;
  scheme_code?: number;
  scheme_name?: string;
  fund_house?: string;
  category?: string;
  weight_pct?: number;
  amount?: number;
  expense_ratio?: number | null;
  ter_status?: string | null;
  sharpe_ratio?: number | null;
  sortino_ratio?: number | null;
  cagr_pct?: number | null;
  max_drawdown_pct?: number | null;
  alpha_vs_sleeve_median_pct?: number | null;
  why?: string;
  error?: string;
}

export interface PortfolioBuildResult {
  error?: string;
  risk_tier?: string;
  budget?: number;
  target_alloc?: Record<string, number>;
  weights?: Record<string, number>;
  picks?: PortfolioPick[];
  folded_notes?: string[];
  method?: string;
  achieved_vol_pct?: number;
  vol_ceiling_pct?: number;
  vol_ceiling_applied?: boolean;
}

export interface BacktestResultRow {
  [key: string]: unknown;
  nav_date: string;
  portfolio_value: number;
  total_invested: number;
}

export interface AdvisorBacktest {
  error?: string;
  sample?: "is" | "oos";
  final_value?: number;
  total_invested?: number;
  n_contributions?: number;
  money_weighted_xirr_pct?: number | null;
  twr_metrics?: {
    cagr_pct: number | null;
    sharpe_ratio: number | null;
    max_drawdown_pct: number | null;
    vol_annualized_pct: number | null;
    costs_model?: string;
  };
  df_result?: BacktestResultRow[];
}

export interface SuggestWindows {
  construction_start: string;
  construction_end: string;
  test_start_effective: string;
  test_end: string;
  construction_interval: string;
  test_interval: string;
  sample: "is" | "oos";
}

export interface SuggestResponse {
  rules_result: PortfolioBuildResult;
  mvo_result: PortfolioBuildResult | null;
  hrp_result: PortfolioBuildResult | null;
  rules_backtest: AdvisorBacktest | null;
  mvo_backtest: AdvisorBacktest | null;
  hrp_backtest: AdvisorBacktest | null;
  risk_budgeting?: Record<string, unknown> | null;
  warnings?: string[];
  windows?: SuggestWindows;
}

export interface SuggestRequest {
  risk_tier: string;
  budget: number;
  mode: "Lump Sum" | "SIP (Monthly)";
  lump_sum_amount: number;
  sip_amount: number;
  start_date: string;
  end_date: string;
  construction_start?: string;
  construction_end?: string;
  test_start?: string;
  test_end?: string;
  holdout?: boolean;
}

export const suggestPortfolio = (req: SuggestRequest) => apiPost<SuggestResponse>("/api/portfolio-advisor/suggest", req);

export interface BlackLittermanRequest {
  scheme_codes?: number[];
  asset_names?: string[];
  prior_weights?: number[];
  views_matrix_P: number[][];
  views_returns_Q: number[];
  views_confidences?: number[];
  start_date?: string;
  end_date?: string;
}

export const runBlackLitterman = (req: BlackLittermanRequest) =>
  apiPost<Record<string, unknown>>("/api/portfolio-advisor/black-litterman", req);

export const getEfficientFrontier = (params: {
  risk_tier: string;
  construction_start?: string;
  construction_end?: string;
  scheme_codes?: string;
}) => apiGet<Record<string, unknown>>("/api/portfolio-advisor/efficient-frontier", params);
