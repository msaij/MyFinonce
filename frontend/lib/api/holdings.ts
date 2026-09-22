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
  total_invested: number;
  total_redeemed: number;
  day_change: number;
  xirr_pct: number | null;
  xirr_note: "too_short" | "no_flows" | "no_solution" | null;
  weight_pct: number | null;
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
  total_gain: number;
  net_contributed: number;
  xirr_pct: number | null;
  xirr_note: string | null;
  day_change: number;
  day_change_pct: number | null;
  open_positions: number;
  transactions: number;
}

export interface HoldingsSummary {
  portfolio_ids: number[];
  as_of: string | null;
  kpis: HoldingsKpis;
  positions: Position[];
}

export interface PositionDetail {
  position: Position | null;
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
  benchmark?: { scheme_code: number | null; scheme_name: string | null };
  periods?: PeriodReturn[];
  attribution?: { total_gain: number; holdings: { scheme_code: number; scheme_name: string | null; gain: number; end_value: number; share_pct: number | null }[] };
  monthly_flows?: { month: string; invested: number; withdrawn: number }[];
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

export interface Allocation {
  total_value: number;
  by_asset_class: AllocationBucket[];
  by_category: AllocationBucket[];
  by_amc: AllocationBucket[];
  by_plan: AllocationBucket[];
  by_option: AllocationBucket[];
  holdings: { scheme_code: number; scheme_name: string | null; asset_class: string; category: string | null; value: number; weight_pct: number | null }[];
  concentration: { hhi: number | null; effective_funds: number | null; top1_pct: number | null; top3_pct: number | null; fund_count: number };
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
  window?: { start: string; end: string; n_trading_days: number };
  risk_free_pct?: number;
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
  drawdown?: { dates: string[]; drawdown_pct: number[]; rolling_vol_pct: (number | null)[] };
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

export interface HoldingsFactors {
  empty: boolean;
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
  initial_value?: number;
  days?: number[];
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
export const getMonteCarlo = (pid: PortfolioKey, years: number) =>
  apiGet<MonteCarlo>(`${base}/portfolios/${pid}/monte-carlo`, { years });


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
