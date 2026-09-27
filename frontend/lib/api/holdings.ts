import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from "./client";

/** A portfolio id, or "all" for the household view. */
export type PortfolioKey = number | "all";

export interface Portfolio {
  id: number;
  name: string;
  owner_label: string | null;
  benchmark_scheme_code: number | null;
  color: string | null;
  notes: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export type TxnType =
  | "BUY"
  | "SIP"
  | "REDEEM"
  | "SWITCH"
  | "SWITCH_IN"
  | "SWITCH_OUT"
  | "DIVIDEND_REINVEST"
  | "DIVIDEND_PAYOUT";

/**
 * Ledger row as the API returns it. Money/units/NAV are NUMERIC in Postgres and
 * arrive as exact decimal *strings* -- parse with Number() only for display maths.
 */
export interface Transaction {
  [key: string]: unknown;
  id: number;
  portfolio_id: number;
  scheme_code: number;
  txn_type: Exclude<TxnType, "SWITCH">;
  trade_date: string;
  amount: string;
  units: string;
  nav: string;
  nav_source: "amfi_auto" | "user";
  stamp_duty: string;
  switch_group: string | null;
  notes: string | null;
  deleted_at: string | null;
  scheme_name?: string | null;
  plan_type?: string | null;
  option_type?: string | null;
  /** "Fund name (Direct - Growth) [AMFI code]", the app-wide label. */
  display_name?: string;
}

export interface TransactionDraft {
  portfolio_id: number;
  scheme_code: number;
  txn_type: TxnType;
  trade_date: string;
  amount?: number | null;
  units?: number | null;
  nav?: number | null;
  apply_stamp_duty?: boolean;
  redeem_all?: boolean;
  switch_to_scheme_code?: number | null;
  notes?: string | null;
}

export interface PreviewResult {
  ok: boolean;
  error: string | null;
  errors: { txn_id: number | null; scheme_code: number; trade_date: string; message: string }[];
  rows: Transaction[];
  warnings: string[];
  units_held_after?: Record<string, number>;
}

export interface PositionFlags {
  stale_nav: boolean;
  regular_plan: boolean;
  split_adjusted: boolean;
}

export interface Position {
  [key: string]: unknown;
  scheme_code: number;
  scheme_name: string | null;
  display_name: string;
  fund_house: string | null;
  category: string | null;
  broad_category: string | null;
  plan_type: string | null;
  option_type: string | null;
  expense_ratio: number | null;
  ter_status: string | null;
  units: number;
  avg_cost_nav: number | null;
  cost_basis: number;
  latest_nav: number | null;
  latest_date: string | null;
  current_value: number;
  unrealised_gain: number;
  unrealised_pct: number | null;
  realised_gain: number;
  dividend_income: number;
  stamp_duty: number;
  total_invested: number;
  total_redeemed: number;
  /** Rupee move on the units held at the previous close; units bought on the latest NAV date earn nothing yet. */
  day_change: number;
  xirr_pct: number | null;
  xirr_note: "too_short" | "no_flows" | "no_solution" | null;
  /** ISO date a withheld XIRR will start to show. */
  xirr_available_on: string | null;
  weight_pct: number | null;
  /** SEBI category without AMFI's section prefix ("Liquid Fund"). */
  sebi_category: string | null;
  asset_class: string;
  /** SEBI riskometer label, e.g. "Low to Moderate". */
  riskometer: string | null;
  /** Value x TER: what the expense ratio costs a year at today's value. */
  annual_fee: number | null;
  first_date: string | null;
  txn_count: number;
  portfolio_ids: number[];
  is_closed: boolean;
  flags: PositionFlags;
}

export interface HoldingsKpis {
  current_value: number;
  invested: number;
  unrealised_gain: number;
  unrealised_pct: number | null;
  realised_gain: number;
  dividend_income: number;
  stamp_duty: number;
  total_gain: number;
  /** Total gain on the cash actually put in, net of withdrawals; null once more came out than went in. */
  total_gain_pct: number | null;
  net_contributed: number;
  xirr_pct: number | null;
  xirr_note: string | null;
  /** ISO date a withheld XIRR will start to show; null when it already shows. */
  xirr_available_on: string | null;
  first_investment_date: string | null;
  /** Average days each rupee put in has been invested (to the valuation date, or to the
   *  redemption that took it out, oldest first), weighted by amount. */
  avg_days_invested: number | null;
  /** Calendar days from the first investment to the valuation date. */
  days_since_first_investment: number | null;
  /** Time-weighted return since the first investment, not annualised. */
  twr_since_start_pct: number | null;
  benchmark_since_start_pct: number | null;
  excess_since_start_pp: number | null;
  benchmark_name: string | null;
  day_change: number;
  day_change_pct: number | null;
  day_benchmark_pct: number | null;
  /** Value-weighted TER over the funds that have one (see ter_coverage_pct). */
  weighted_ter_pct: number | null;
  annual_fee: number | null;
  /** Share of today's value whose fund has a TER on record. */
  ter_coverage_pct: number | null;
  open_positions: number;
  transactions: number;
}

export interface HoldingsSummary {
  portfolio_ids: number[];
  as_of: string | null;
  kpis: HoldingsKpis;
  positions: Position[];
}

export interface TerContext {
  ter_pct: number | null;
  breakdown: { base_expense_ratio?: number; brokerage_cost_pct?: number; transaction_cost_pct?: number; statutory_levies_pct?: number } | null;
  as_of: string | null;
  category_spread: { peers: number; p10: number; median: number; p90: number } | null;
  status: "ok" | "high" | "low" | "inconsistent" | "unverified" | "missing";
  reason: string | null;
}

export interface PositionDetail {
  position: Position | null;
  ter: TerContext | null;
  lots: { portfolio_id: number; txn_id: number; date: string; units: number; cost: number; cost_nav: number | null }[];
  transactions: (Transaction & { effective_units: number; units_scale: number; balance_units: number })[];
  nav_series: { date: string; nav: number }[];
}

export interface PeriodReturn {
  label: string;
  start: string;
  annualised: boolean;
  twr_pct: number | null;
  benchmark_pct: number | null;
  excess_pct: number | null;
  xirr_pct: number | null;
}

export interface Performance {
  empty: boolean;
  as_of?: string;
  series?: {
    dates: string[];
    value: (number | null)[];
    invested: (number | null)[];
    twr_index: (number | null)[];
    benchmark_index: (number | null)[] | null;
  };
  benchmark?: {
    scheme_code: number | null;
    scheme_name: string | null;
    /** "category_blend": each fund vs its SEBI category average; "scheme": a benchmark you chose. */
    kind: "scheme" | "category_blend";
    components: { category: string; weight_pct: number }[];
  };
  periods?: PeriodReturn[];
  attribution?: {
    total_gain: number;
    gross_gain: number;
    gross_loss: number;
    holdings: {
      scheme_code: number;
      scheme_name: string | null;
      gain: number;
      end_value: number;
      /** Share of the gross gain (winners sum to 100) or of the gross loss (losers sum to -100). */
      gain_share_pct: number;
      /** Share of today's portfolio value; 0 for a fund fully exited. */
      weight_pct: number | null;
    }[];
  };
}

export interface AllocationBucket {
  bucket: string;
  value: number;
  weight_pct: number;
}

export interface DriftRow {
  asset_class: string;
  actual_pct: number;
  target_pct: number;
  drift_pct: number;
  status: "over" | "under" | "ok";
}

export interface PortfolioSplit extends AllocationBucket {
  portfolio_id: number;
  fund_count: number;
  by_asset_class: Record<string, number>;
}

export interface AllocationHolding {
  scheme_code: number;
  scheme_name: string | null;
  asset_class: string;
  category: string | null;
  fund_house: string | null;
  riskometer: string | null;
  value: number;
  weight_pct: number | null;
}

export interface Allocation {
  total_value: number;
  /** Weights are by market value today (units x latest NAV). */
  basis: "current_value";
  as_of: string | null;
  by_asset_class: AllocationBucket[];
  by_category: AllocationBucket[];
  by_amc: AllocationBucket[];
  by_plan: AllocationBucket[];
  by_option: AllocationBucket[];
  /** Household view with 2+ portfolios only. */
  by_portfolio: PortfolioSplit[] | null;
  holdings: AllocationHolding[];
  concentration: {
    hhi: number | null;
    effective_funds: number | null;
    top1_pct: number | null;
    top3_pct: number | null;
    fund_count: number;
    amc_count: number;
    top_amc: string | null;
    top_amc_pct: number | null;
  };
  asset_classes: string[];
  targets: Record<string, number> | null;
  drift: DriftRow[] | null;
  drift_band_pct: number;
}

export interface RebalanceResult {
  new_money: number;
  note: string;
  rows: { asset_class: string; amount: number; before_pct: number; after_pct: number; target_pct: number; suggested_scheme_code: number | null; suggested_scheme_name: string | null }[];
}

export interface RiskMetrics {
  [key: string]: number | null | undefined;
  cagr_pct: number;
  vol_annualized_pct: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number | null;
  max_drawdown_pct: number;
  current_drawdown_pct: number;
  var_95_daily_pct: number;
  cvar_95_ann_pct: number;
  cf_var_99_daily_pct: number;
}

export interface HoldingsRisk {
  empty: boolean;
  insufficient?: boolean;
  message?: string;
  /** "realised": your own track record; "current_mix": today's weights replayed over the funds' history. */
  basis?: "realised" | "current_mix";
  basis_note?: string | null;
  /** obs_per_year is the series' own sampling rate -- ~365 when liquid funds (which price
   *  every calendar day) set the grid, ~252 for a trading-day series. Every annualised
   *  figure on the page is scaled by it, so anything that explains one must use it too. */
  window?: { start: string; end: string; n_trading_days: number; obs_per_year?: number };
  risk_free_pct?: number;
  /** SEBI's riskometer for each held fund, and where the money sits on the scale. Present
   *  even when `insufficient`: it needs no price history. */
  riskometer?: Riskometer;
  metrics?: RiskMetrics;
  benchmark?: { scheme_code: number | null; scheme_name: string | null };
  relative?: {
    beta: number;
    alpha_annualized_pct: number;
    r_squared: number;
    tracking_error_pct: number;
    information_ratio: number;
    up_market_capture_pct: number;
    down_market_capture_pct: number;
  } | null;
  /** coverage_pct: on the hypothetical current-mix basis, the share of today's money whose
   *  funds already existed on each date; null on the realised basis, which is 100% by definition. */
  drawdown?: { dates: string[]; drawdown_pct: number[]; rolling_vol_pct: (number | null)[]; coverage_pct?: (number | null)[] | null };
  holdings?: {
    available: boolean;
    reason?: string;
    window_start?: string;
    n_days?: number;
    codes?: number[];
    names?: string[];
    correlation?: number[][];
    risk_contributions?: { scheme_code: number; scheme_name: string; weight_pct: number; risk_pct: number }[];
    portfolio_vol_pct?: number;
    effective_funds?: number;
    effective_bets?: number;
    redundant_pairs?: { a: number; a_name: string; b: number; b_name: string; correlation: number; category: string }[];
    excluded_short_history?: number[];
  };
}

export interface Riskometer {
  available: boolean;
  reason?: string;
  levels?: string[];
  funds?: { scheme_code: number; scheme_name: string; level: string | null; rank: number | null; weight_pct: number; as_of: string | null }[];
  distribution?: { level: string; rank: number; weight_pct: number }[];
  /** Money-weighted position on the 1-6 scale; a summary, not an official SEBI figure. */
  weighted_rank?: number;
  portfolio_level?: string;
  highest?: { level: string; scheme_name: string; weight_pct: number };
  /** Share of your money in funds AMFI has published a riskometer for. */
  coverage_pct?: number;
  as_of?: string | null;
}

export interface HoldingsFactors {
  empty: boolean;
  basis?: "realised" | "current_mix";
  basis_note?: string | null;
  unavailable?: boolean;
  reason?: string;
  window?: { start: string; end: string };
  regression?: {
    alpha_annualized_pct: number;
    r_squared: number;
    factor_betas: Record<string, number>;
    p_values: Record<string, number>;
    systematic_risk_pct: number;
    idiosyncratic_risk_pct: number;
    waterfall_data: { labels: string[]; values: number[]; measures: ("relative" | "total")[]; text?: string[] };
    n_observations: number;
  };
}

export interface StressScenario {
  id: string;
  name: string;
  window_start: string;
  window_end: string;
  /** What happened in markets during this window. */
  description?: string | null;
  available: boolean;
  reason: string | null;
  drawdown_pct: number | null;
  benchmark_drawdown_pct: number | null;
  rupee_impact: number | null;
  recovery_days: number | null;
  recovered: boolean | null;
  downside_beta: number | null;
  coverage_pct: number;
}

export interface HoldingsStress {
  empty: boolean;
  hypothetical?: boolean;
  current_value?: number;
  scenarios?: StressScenario[];
  parametric?: { available: boolean; betas?: Record<string, number>; total_return_impact_pct?: number; rupee_impact?: number; projected_capital?: number; factor_impacts_pct?: Record<string, number> };
}

export interface MonteCarlo {
  empty: boolean;
  insufficient?: boolean;
  years?: number;
  /** The horizon actually simulated, in calendar days. */
  horizon_days?: number;
  initial_value?: number;
  /** Simulation step indices. Steps are drawn at the series' own frequency, so they convert
   *  to years with obs_per_year -- dividing by 252 stretched a "1Y" run to 1.45 years. */
  days?: number[];
  obs_per_year?: number;
  p5?: number[];
  p25?: number[];
  p50?: number[];
  p75?: number[];
  p95?: number[];
  prob_profit_pct?: number;
  median_terminal?: number;
}

export interface Shocks {
  market: number;
  size: number;
  value: number;
  momentum: number;
}

export interface Insight {
  kind: string;
  severity: "danger" | "warning" | "info";
  title: string;
  detail: string;
  scheme_codes: number[];
  numbers: Record<string, unknown>;
}

export interface SipMandate {
  id: number;
  portfolio_id: number;
  scheme_code: number;
  amount: string;
  day_of_month: number;
  start_date: string;
  end_date: string | null;
  step_up_pct: string;
  active: boolean;
  notes: string | null;
  scheme_name: string | null;
  current_amount: number;
  instalments_recorded: number;
  instalments_pending: number;
  upcoming: { date: string; amount: number }[];
}

export interface GenerateResult {
  mandate_id: number;
  count: number;
  total_amount: number;
  rows: Transaction[];
  skipped: { scheduled_date: string; reason: string }[];
  written: boolean;
}

export interface Goal {
  id: number;
  name: string;
  target_amount: string;
  target_date: string;
  inflation_pct: string;
  notes: string | null;
  archived: boolean;
  portfolio_ids: number[];
}

export interface GoalStatus {
  goal: Goal;
  state: "projected" | "no_portfolios" | "reached" | "past_due" | "insufficient_history";
  years_left: number;
  current_value?: number;
  target_today?: number;
  target_future?: number;
  inflation_pct?: number;
  current_sip?: number;
  sip_used?: number;
  step_up_pct?: number;
  progress_pct?: number | null;
  probability_pct?: number;
  median_terminal?: number;
  required_sip?: { p50: number; p75: number; p90: number };
  projection?: { month: number[]; p10: number[]; p50: number[]; p90: number[]; contributed: number[] };
}

export interface AlertRule {
  id: number;
  portfolio_id: number | null;
  kind: "drift" | "drawdown" | "stale_nav" | "regular_plan";
  threshold: string | null;
  active: boolean;
}

export interface HoldingAlert {
  id: number;
  rule_id: number;
  severity: "danger" | "warning" | "info";
  message: string;
  fired_at: string;
  ack_at: string | null;
}

const base = "/api/holdings";

export const getInsights = (pid: PortfolioKey) => apiGet<{ insights: Insight[]; as_of: string | null }>(`${base}/portfolios/${pid}/insights`);

export const listSipMandates = (pid: PortfolioKey) => apiGet<SipMandate[]>(`${base}/portfolios/${pid}/sip-mandates`);
export const createSipMandate = (body: {
  portfolio_id: number;
  scheme_code: number;
  amount: number;
  day_of_month: number;
  start_date: string;
  end_date?: string | null;
  step_up_pct?: number;
}) => apiPost<SipMandate>(`${base}/sip-mandates`, body);
export const updateSipMandate = (id: number, body: Partial<{ active: boolean; amount: number; end_date: string | null; step_up_pct: number }>) =>
  apiPatch<SipMandate>(`${base}/sip-mandates/${id}`, body);
export const generateInstalments = (id: number, confirm: boolean) =>
  apiPost<GenerateResult>(`${base}/sip-mandates/${id}/generate?confirm=${confirm}`, {});

export const listGoals = () => apiGet<Goal[]>(`${base}/goals`);
export const saveGoal = (id: number | null, body: { name: string; target_amount: number; target_date: string; inflation_pct: number; portfolio_ids: number[]; archived?: boolean }) =>
  id === null ? apiPost<Goal>(`${base}/goals`, body) : apiPut<Goal>(`${base}/goals/${id}`, body);
export const deleteGoal = (id: number) => apiDelete<{ deleted: number }>(`${base}/goals/${id}`);
export const getGoalStatus = (id: number, sip?: number | null) =>
  apiGet<GoalStatus>(`${base}/goals/${id}/status`, { sip: sip ?? undefined });

export const listAlertRules = () => apiGet<{ rules: AlertRule[]; kinds: Record<string, string> }>(`${base}/alert-rules`);
export const createAlertRule = (body: { kind: AlertRule["kind"]; portfolio_id: number | null; threshold?: number | null }) =>
  apiPost<AlertRule>(`${base}/alert-rules`, body);
export const deleteAlertRule = (id: number) => apiDelete<{ deleted: number }>(`${base}/alert-rules/${id}`);
export const listAlerts = (unacked = false) => apiGet<{ alerts: HoldingAlert[]; unacked: number }>(`${base}/alerts`, { unacked: unacked || undefined });
export const getAlertCount = () => apiGet<{ unacked: number }>(`${base}/alerts/count`);
export const ackAlerts = (ids?: number[]) => apiPost<{ acknowledged: number }>(`${base}/alerts/ack`, { ids: ids ?? null });

export const getHoldingsRisk = (pid: PortfolioKey) => apiGet<HoldingsRisk>(`${base}/portfolios/${pid}/risk`);
export const getHoldingsFactors = (pid: PortfolioKey) => apiGet<HoldingsFactors>(`${base}/portfolios/${pid}/factors`);
export const getHoldingsStress = (pid: PortfolioKey, s: Shocks) =>
  apiGet<HoldingsStress>(`${base}/portfolios/${pid}/stress`, {
    shock_market: s.market,
    shock_size: s.size,
    shock_value: s.value,
    shock_momentum: s.momentum,
  });
/** `horizonDays`: calendar days from today, 30 (one month) to 1826 (five years). */
export const getMonteCarlo = (pid: PortfolioKey, horizonDays: number) =>
  apiGet<MonteCarlo>(`${base}/portfolios/${pid}/monte-carlo`, { days: horizonDays });


export const getPerformance = (pid: PortfolioKey) => apiGet<Performance>(`${base}/portfolios/${pid}/performance`);
export const getAllocation = (pid: PortfolioKey) => apiGet<Allocation>(`${base}/portfolios/${pid}/allocation`);
export const putTargets = (id: number, targets: Record<string, number>) =>
  apiPut<{ targets: Record<string, number>; asset_classes: string[] }>(`${base}/portfolios/${id}/targets`, { targets });
export const getRebalance = (id: number, newMoney: number) =>
  apiGet<RebalanceResult>(`${base}/portfolios/${id}/rebalance`, { new_money: newMoney });

export const listPortfolios = (includeArchived = false) =>
  apiGet<Portfolio[]>(`${base}/portfolios`, { include_archived: includeArchived || undefined });
export const createPortfolio = (body: { name: string; owner_label?: string | null; color?: string | null }) =>
  apiPost<Portfolio>(`${base}/portfolios`, body);
export const updatePortfolio = (id: number, body: Partial<Pick<Portfolio, "name" | "owner_label" | "color" | "notes" | "archived" | "benchmark_scheme_code">>) =>
  apiPatch<Portfolio>(`${base}/portfolios/${id}`, body);
export const archivePortfolio = (id: number) => apiDelete<Portfolio>(`${base}/portfolios/${id}`);

export const getHoldingsSummary = (pid: PortfolioKey) => apiGet<HoldingsSummary>(`${base}/portfolios/${pid}/summary`);
export const getPositionDetail = (pid: PortfolioKey, code: number) =>
  apiGet<PositionDetail>(`${base}/portfolios/${pid}/positions/${code}`);
/** One trailing window on the Holdings tab. Counted in NAV days, and time-weighted, so
 *  money paid in during the window is not mistaken for a gain. */
export interface RecentChangeWindow {
  days: number;
  available: boolean;
  start: string | null;
  change_pct: number | null;
  avg_daily_pct: number | null;
  gain: number | null;
  /** The same window of the peer benchmark the Performance tab plots. */
  benchmark_change_pct: number | null;
  /** Portfolio minus benchmark, in percentage points. */
  excess_pp: number | null;
  nav_days_held: number;
  /** NAV days still to go before this window can be shown; 0 once it is available. */
  days_needed: number;
}

export interface RecentChanges {
  empty: boolean;
  as_of?: string;
  benchmark_name?: string | null;
  windows: RecentChangeWindow[];
}

export const getRecentChanges = (pid: PortfolioKey) =>
  apiGet<RecentChanges>(`/api/holdings/portfolios/${pid}/recent-changes`);

export const listTransactions = (pid: PortfolioKey, includeDeleted = false) =>
  apiGet<Transaction[]>(`${base}/portfolios/${pid}/transactions`, { include_deleted: includeDeleted || undefined });

export const previewTransaction = (draft: TransactionDraft, replaceTxnId?: number) =>
  apiPost<PreviewResult>(`${base}/transactions/preview`, { ...draft, replace_txn_id: replaceTxnId ?? null });
export const addTransaction = (draft: TransactionDraft) =>
  apiPost<{ transactions: Transaction[]; warnings: string[] }>(`${base}/transactions`, draft);
export const editTransaction = (id: number, draft: TransactionDraft) =>
  apiPatch<{ transaction: Transaction; warnings: string[] }>(`${base}/transactions/${id}`, draft);
export const deleteTransaction = (id: number) => apiDelete<{ deleted_ids: number[] }>(`${base}/transactions/${id}`);
export const restoreTransaction = (id: number) => apiPost<{ restored_ids: number[] }>(`${base}/transactions/${id}/restore`, {});

export const exportCsvUrl = (pid: PortfolioKey) => `${base}/portfolios/${pid}/export.csv`;
export const backupUrl = `${base}/backup.json`;
export const restoreBackup = (payload: unknown) =>
  apiPost<{ portfolios: number; holding_transactions: number }>(`${base}/restore`, payload);
