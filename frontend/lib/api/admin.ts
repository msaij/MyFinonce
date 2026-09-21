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

export interface AdminStatus {
  stats: DatabaseStats;
  staleness: Staleness;
  sync_history: SyncHistory;
  ter_sync_history: SyncHistory;
  cost_coverage: CostCoverage;
  ter_backfill_status: TerBackfillStatus;
  backfill_status: HistoricalBackfillStatus;
  backfill_progress: HistoricalBackfillProgress;
  ter_portal_url: string;
  enable_sync_daemon: boolean;
}

export const getAdminStatus = () => apiGet<AdminStatus>("/api/admin/status");

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

export const getBackfillChunkCount = (start_year: number) =>
  apiGet<{ start_year: number; total_chunks: number }>("/api/admin/backfill/chunk-count", { start_year });
export const stopHistoricalBackfill = () => apiPost<{ success: boolean; message: string }>("/api/admin/backfill/historical/stop", {});

export const getFactorProxies = () => apiGet<{ proxies: Record<string, number>; defaults: Record<string, number> }>("/api/admin/factor-proxies");
export const setFactorProxies = (body: Record<string, number>) =>
  apiPost<{ success: boolean; proxies: Record<string, number> }>("/api/admin/factor-proxies", body);
