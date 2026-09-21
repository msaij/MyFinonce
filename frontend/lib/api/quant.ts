import { apiGet } from "./client";

export interface PlotlyFigure {
  data: unknown[];
  layout?: Record<string, unknown>;
}

export type BenchMode = "Category Benchmark (Synthesized Peer Average)" | "Nifty 50 Index Fund Proxy" | "Custom Peer Mutual Fund";

export interface QuantSchemeProfile {
  scheme_code: number;
  scheme_name: string;
  fund_house?: string;
  category?: string | null;
  broad_category?: string | null;
  plan_type?: string;
  option_type?: string;
  expense_ratio?: number | null;
  ter_status?: string | null;
  latest_nav?: number | null;
  latest_date?: string | null;
  [key: string]: unknown;
}

export interface QuantCoverage {
  n_trading_days: number;
  actual_start: string;
  actual_end: string;
  is_partial: boolean;
  requested_days: number | null;
}

export interface RiskAdjustedMetrics {
  n_trading_days: number;
  total_days: number;
  total_return_pct: number;
  cagr_pct: number;
  vol_annualized_pct: number;
  vol_daily_pct: number;
  downside_dev_ann_pct: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number | null;
  max_drawdown_pct: number;
  current_drawdown_pct: number;
  var_95_daily_pct: number;
  var_95_ann_pct: number;
  var_99_daily_pct: number;
  cvar_95_daily_pct: number;
  cvar_95_ann_pct: number;
  skewness: number;
  kurtosis: number;
  win_rate_pct: number;
  best_day_pct: number;
  worst_day_pct: number;
  gain_to_pain_ratio: number | null;
  profit_factor: number | null;
  avg_gain_pct: number;
  avg_loss_pct: number;
}

export interface BenchmarkRelativeMetrics {
  beta: number;
  alpha_annualized_pct: number;
  correlation: number;
  r_squared: number;
  tracking_error_pct: number;
  information_ratio: number;
  treynor_ratio: number;
  up_market_capture_pct: number;
  down_market_capture_pct: number;
  capture_ratio: number | null;
  benchmark_cagr_pct: number;
  excess_cagr_pct: number;
  common_trading_days: number;
}

export interface BenchmarkResult {
  label: string;
  available: boolean;
  unavailable_reason: string | null;
  metrics: BenchmarkRelativeMetrics | null;
}

export interface QuantIntelligenceQuadrant {
  title: string;
  badge: string;
  color: string;
  description: string;
}

export interface QuantIntelligence {
  executive_verdict: string;
  quadrant: QuantIntelligenceQuadrant;
  return_drivers: {
    title: string;
    text: string;
    sharpe: number;
    sortino: number;
    beta: number | null;
    alpha: number | null;
  };
  style_drift: {
    title: string;
    assessment: string;
    text: string;
    r_squared: number | null;
    tracking_error_pct: number | null;
    information_ratio: number | null;
  };
  tail_risk: {
    title: string;
    text: string;
    skewness: number;
    kurtosis: number;
    var_95_daily_pct: number;
    cvar_95_ann_pct: number;
    calmar_ratio: number | null;
    max_drawdown_pct: number;
  };
  fee_drag: {
    title: string;
    text: string;
    ter_pct: number | null;
    is_direct: boolean;
    gross_alpha_pct: number | null;
  };
  market_capture: {
    title: string;
    verdict: string;
    text: string;
    up_market_capture_pct: number | null;
    down_market_capture_pct: number | null;
    capture_ratio: number | null;
  };
}

export interface QuantFigures {
  cumulative_return: PlotlyFigure | null;
  distribution: PlotlyFigure | null;
  drawdown: PlotlyFigure | null;
  capm_regression: PlotlyFigure | null;
  capture: PlotlyFigure | null;
  rolling_volatility: PlotlyFigure | null;
  rolling_sharpe: PlotlyFigure | null;
}

export interface QuantAnalysisResult {
  error?: string;
  profile: QuantSchemeProfile;
  coverage: QuantCoverage;
  metrics: RiskAdjustedMetrics;
  benchmark: BenchmarkResult;
  gross_alpha_pct: number | null;
  rolling_window: number;
  intelligence?: QuantIntelligence;
  figures: QuantFigures;
}

export interface MonteCarloResult {
  error?: string;
  prob_profit_pct: number;
  prob_beat_inflation_pct: number;
  prob_beat_12pct: number;
  median_terminal: number;
  expected_terminal?: number;
  var_95_capital: number;
  ci_90?: [number, number];
  ci_50?: [number, number];
  worst_case_p5?: number;
  best_case_p95?: number;
  figure: PlotlyFigure;
}

export interface QuantAnalysisParams {
  startDate: string;
  endDate: string;
  benchMode?: BenchMode;
  customPeerCode?: number;
  riskFreeRatePct?: number;
}

export const getQuantAnalysis = (schemeCode: number, params: QuantAnalysisParams) =>
  apiGet<QuantAnalysisResult>(`/api/quant/${schemeCode}`, {
    start_date: params.startDate,
    end_date: params.endDate,
    bench_mode: params.benchMode,
    custom_peer_code: params.customPeerCode,
    risk_free_rate_pct: params.riskFreeRatePct,
  });

export const getMonteCarlo = (schemeCode: number, startDate: string, endDate: string, seed?: number) =>
  apiGet<MonteCarloResult>(`/api/quant/${schemeCode}/monte-carlo`, {
    start_date: startDate,
    end_date: endDate,
    seed,
  });

export interface FactorAttributionResult {
  alpha_annualized_pct: number;
  factor_betas: {
    market: number;
    size: number;
    value: number;
    momentum: number;
    [key: string]: number;
  };
  t_stats: Record<string, number>;
  p_values: Record<string, number>;
  r_squared: number;
  adj_r_squared: number;
  variance_decomposition: {
    market: number;
    size: number;
    value: number;
    momentum: number;
    [key: string]: number;
  };
  systematic_risk_pct: number;
  idiosyncratic_risk_pct: number;
  waterfall_data: {
    labels: string[];
    values: number[];
    measures: ("relative" | "total")[];
    text?: string[];
  };
}

export interface FactorSource {
  market_scheme_code?: number | null;
  momentum_scheme_code?: number | null;
  smb_method?: string;
  hml_method?: string;
  wml_method?: string;
  includes_analysed_scheme_in_smb_hml?: boolean;
  n_obs?: number;
  disclosure?: string;
  missing?: string[];
}

export interface FactorAttributionResponse {
  scheme_code: number;
  scheme_name: string;
  category: string;
  window: {
    start: string;
    end: string;
    n_trading_days: number;
  };
  source?: FactorSource;
  regression: FactorAttributionResult;
  figures: {
    factor_waterfall?: PlotlyFigure;
    factor_decomposition?: PlotlyFigure;
    [key: string]: PlotlyFigure | undefined;
  };
}

export interface ScenarioReplayResult {
  id?: string;
  name?: string;
  scenario?: string;
  scenario_id?: string;
  scenario_name?: string;
  window?: {
    start: string;
    end: string;
  };
  window_start?: string;
  window_end?: string;
  peak_date: string;
  trough_date: string;
  max_drawdown_pct: number;
  benchmark_drawdown_pct: number;
  excess_drawdown_pct: number;
  downside_beta: number;
  recovery_days: number;
  recovered?: boolean;
  is_recovered?: boolean;
  recovery_date?: string | null;
  available?: boolean;
  has_data?: boolean;
}

export interface ParametricSimulationResult {
  simulated_return_delta_pct: number;
  factor_contributions_pct: Record<string, number>;
  stressed_portfolio_cagr_impact: number;
  rupee_capital_impact: number;
  projected_capital_terminal_value: number;
  base_capital: number;
}

export interface StressTestResponse {
  scheme_code: number;
  scheme_name: string;
  category: string;
  scenarios: ScenarioReplayResult[] | Record<string, ScenarioReplayResult>;
  parametric_simulation: ParametricSimulationResult;
  figure: PlotlyFigure;
}

export interface FactorAttributionParams {
  rfDaily?: number;
  start?: string;
  end?: string;
  riskFreeRatePct?: number;
}

export interface StressTestParams {
  shock_market?: number;
  shock_size?: number;
  shock_value?: number;
  shock_momentum?: number;
  market?: number;
  size?: number;
  value?: number;
  momentum?: number;
  is_percentage?: boolean;
}

export function getFactorAttribution(
  schemeCode: number,
  rfDailyOrParams?: number | FactorAttributionParams,
  start?: string,
  end?: string,
  riskFreeRatePct?: number
): Promise<FactorAttributionResponse> {
  let queryParams: Record<string, string | number | undefined> = {};

  if (typeof rfDailyOrParams === "object" && rfDailyOrParams !== null) {
    const p = rfDailyOrParams;
    queryParams = {
      start: p.start,
      end: p.end,
      risk_free_rate_pct: p.riskFreeRatePct ?? (p.rfDaily !== undefined ? ((1 + p.rfDaily) ** 252 - 1) * 100 : undefined),
    };
  } else {
    const rfDaily = rfDailyOrParams;
    const computedRfPct = riskFreeRatePct ?? (rfDaily !== undefined ? ((1 + rfDaily) ** 252 - 1) * 100 : undefined);
    queryParams = {
      start,
      end,
      risk_free_rate_pct: computedRfPct,
    };
  }

  return apiGet<FactorAttributionResponse>(`/api/quant/${schemeCode}/factors`, queryParams);
}

export function getStressTest(
  schemeCode: number,
  shocksOrParams?: StressTestParams | number,
  shockSize?: number,
  shockValue?: number,
  shockMomentum?: number
): Promise<StressTestResponse> {
  let queryParams: Record<string, string | number | boolean | undefined> = {};

  if (typeof shocksOrParams === "object" && shocksOrParams !== null) {
    const p = shocksOrParams as Record<string, number | boolean | undefined>;
    queryParams = {
      shock_market: p.shock_market ?? p.market,
      shock_size: p.shock_size ?? p.size,
      shock_value: p.shock_value ?? p.value,
      shock_momentum: p.shock_momentum ?? p.momentum,
      is_percentage: p.is_percentage as boolean | undefined,
    };
  } else if (typeof shocksOrParams === "number") {
    queryParams = {
      shock_market: shocksOrParams,
      shock_size: shockSize,
      shock_value: shockValue,
      shock_momentum: shockMomentum,
    };
  }

  return apiGet<StressTestResponse>(`/api/quant/${schemeCode}/stress-test`, queryParams);
}

export const getFeeDrag = (schemeCode: number, pairedSchemeCode?: number) =>
  apiGet<Record<string, unknown>>(`/api/quant/${schemeCode}/fee-drag`, {
    paired_scheme_code: pairedSchemeCode,
  });

export const getTailRisk = (schemeCode: number, startDate?: string, endDate?: string) =>
  apiGet<Record<string, unknown>>(`/api/quant/${schemeCode}/tail-risk`, {
    start_date: startDate,
    end_date: endDate,
  });

