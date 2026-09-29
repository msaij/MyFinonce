import { apiGet, apiPost } from "./client";

export interface DatabaseStats {
  schemes_count: number;
  nav_count: number;
  amc_count: number;
  category_count: number;
  min_date: string | null;
  max_date: string | null;
  file_size_mb: number;
  db_path: string;
}

export interface Staleness {
  is_stale: boolean;
  current_max_date: string | null;
  expected_date: string | null;
}

export interface SyncHistory {
  last_attempt_at: string | null;
  last_attempt_trigger: string | null;
  last_success_at: string | null;
  last_success_msg: string | null;
  last_failure_at: string | null;
  last_error: string | null;
  total_syncs: number;
  total_failures: number;
}

export interface CostCoverage {
  total_schemes: number;
  ter_official: number;
  ter_auto_synced: number;
  ter_legacy: number;
  ter_unknown: number;
}

export interface TerBackfillStatus {
  is_running: boolean;
  should_stop: boolean;
  total_months: number;
  current_month_idx: number;
  current_month_str: string;
  last_error: string | null;
  started_at: number | null;
  finished_at: number | null;
}

export interface HistoricalBackfillStatus {
  is_running: boolean;
  should_stop: boolean;
  /** Chunks left to do in THIS run -- already-completed ones are excluded. */
  total_chunks: number;
  current_chunk_idx: number;
  current_chunk_str: string;
  records_added: number;
  schemes_added: number;
  /** Chunks this run skipped because a previous run had already finished them. */
  skipped_chunks?: number;
  last_error: string | null;
  started_at: number | null;
  finished_at: number | null;
}

/** Durable backfill progress, unlike HistoricalBackfillStatus, which only describes
 *  the current process's run. Survives a stop, a crash and a container restart, so
 *  this is what tells the user how much of the multi-year range they actually hold. */
export interface HistoricalBackfillProgress {
  completed_chunks: number;
  completed_units: string[];
}

/** Coverage over schemes still publishing NAVs, not every scheme ever listed. */
export interface LiveCoverage {
  live_schemes: number;
  listed_schemes: number;
  ter_official: number;
  riskometer: number;
  riskometer_as_of: string | null;
  /** Live schemes AMFI states no Direct/Regular plan for. Optional because the backend is
   *  restarted separately from a frontend rebuild, and an ops page must not crash on skew. */
  unknown_plan?: number;
  /** Live schemes with no confirmed Growth/IDCW option. */
  unknown_option?: number;
}

export interface AdminStatus {
  stats: DatabaseStats;
  staleness: Staleness;
  sync_history: SyncHistory;
  ter_sync_history: SyncHistory;
  cost_coverage: CostCoverage;
  live_coverage: LiveCoverage;
  sync_job: SyncJobStatus;
  ter_backfill_status: TerBackfillStatus;
  backfill_status: HistoricalBackfillStatus;
  backfill_progress: HistoricalBackfillProgress;
  ter_portal_url: string;
  enable_sync_daemon: boolean;
}

/** One step of a full refresh. Each source reports its own outcome: a TER outage must not
 *  read as "your NAVs are stale". */
export interface StepResult {
  ok: boolean;
  message: string;
}

export interface FullRefreshResult {
  ok: boolean;
  trigger: string;
  elapsed_seconds: number;
  nav: StepResult;
  ter: StepResult;
  resolve: {
    ok: boolean;
    reason?: string;
    resolved?: number;
    plan_changed?: number;
    option_changed?: number;
  };
}

export type SyncJobKind = "full" | "nav" | "ter" | "catchup";

/** One completed sync job, kept in the database so it survives restarts. */
export interface SyncJobRecord {
  kind: SyncJobKind;
  /** "manual", "scheduled_00:05", "scheduled_23:30", "heartbeat", "startup". */
  trigger: string;
  ok: boolean;
  message: string;
  started_at: number;
  finished_at: number;
  elapsed_seconds: number;
  /** For a full refresh, the per-step outcome. */
  result: (Partial<FullRefreshResult> & { message?: string; filled?: number }) | null;
}

/** Every sync -- scheduled, catch-up or a button -- runs as a job through one tracker, one
 *  at a time, in the background; the page polls this out of /status. */
export interface SyncJobStatus {
  is_running: boolean;
  kind: SyncJobKind | null;
  label: string | null;
  trigger: string | null;
  step: string;
  started_at: number | null;
  last: Record<SyncJobKind, SyncJobRecord | null>;
}

export interface Verification {
  checked_at: string;
  nav: { rows: number; schemes: number; first_date: string | null; last_date: string | null; expected_date: string | null; lag_days: number | null };
  schemes: { total: number; active: number; unknown_plan: number; unknown_option: number; ter_official: number };
  backfill: { completed_chunks: number };
  findings: { level: "warning" | "info"; text: string }[];
}

/** One offending row. `values` carries whatever makes the contradiction visible (the two
 *  TERs of a Direct/Regular pair, the ISIN two codes share), so its keys vary by check. */
export interface AuditSample {
  scheme_code: number | null;
  label: string;
  values: Record<string, string | number | boolean | null>;
}

export interface AuditCheck {
  name: string;
  title: string;
  severity: "error" | "warning" | "info";
  /** "not_run": the check's input does not exist yet. "error": the check's own query failed. */
  status: "pass" | "flagged" | "not_run" | "error";
  count: number | null;
  description: string;
  samples: AuditSample[];
  detail: Record<string, unknown> | null;
  elapsed_ms: number | null;
}

export interface AuditRun {
  /** True until the first audit has been stored; every other field is then empty. */
  empty: boolean;
  run_id: number | null;
  run_at: string | null;
  trigger: string | null;
  elapsed_ms: number | null;
  summary: { errors: number; error_rows: number; warnings: number; infos: number; check_failures: number } | null;
  checks: AuditCheck[];
}

export const getAdminStatus = () => apiGet<AdminStatus>("/api/admin/status");

export const getLatestAudit = () => apiGet<AuditRun>("/api/admin/audit/latest");
export const runAudit = () => apiPost<AuditRun>("/api/admin/audit", {});

export const runFullRefresh = () => apiPost<{ success: boolean; message: string }>("/api/admin/sync/all", {});
export const getVerification = () => apiGet<Verification>("/api/admin/verify");

export const triggerDailySync = () => apiPost<{ success: boolean; message: string }>("/api/admin/sync/daily", {});
export const triggerTerSync = () => apiPost<{ success: boolean; message: string }>("/api/admin/sync/ter", {});
export const recomputeSummary = () => apiPost<{ success: boolean; message: string }>("/api/admin/recompute-summary", {});

export const startTerBackfill = (n_months: number) => apiPost<{ success: boolean }>("/api/admin/backfill/ter/start", { n_months });
export const stopTerBackfill = () => apiPost<{ success: boolean; message: string }>("/api/admin/backfill/ter/stop", {});

/** `resume` defaults to true server-side: date ranges a previous run already
 *  finished are skipped rather than re-downloaded. Pass false only to force a
 *  full re-download of the whole range. */
export const startHistoricalBackfill = (start_year: number, max_chunks?: number, resume = true) =>
  apiPost<{ success: boolean }>("/api/admin/backfill/historical/start", { start_year, max_chunks, resume });

/** What "since <year>" means in months, answered by the engine that will fetch them --
 *  including AMFI's own 2018 floor, so the button never promises months that do not exist. */
export const getTerBackfillMonthCount = (start_year: number) =>
  apiGet<{ start_year: number; total_months: number; oldest_month: string | null; newest_month: string | null }>(
    "/api/admin/backfill/ter/month-count",
    { start_year }
  );

export const getBackfillChunkCount = (start_year: number) =>
  apiGet<{ start_year: number; total_chunks: number }>("/api/admin/backfill/chunk-count", { start_year });
export const stopHistoricalBackfill = () => apiPost<{ success: boolean; message: string }>("/api/admin/backfill/historical/stop", {});

export const getFactorProxies = () => apiGet<{ proxies: Record<string, number>; defaults: Record<string, number> }>("/api/admin/factor-proxies");
export const setFactorProxies = (body: Record<string, number>) =>
  apiPost<{ success: boolean; proxies: Record<string, number> }>("/api/admin/factor-proxies", body);
