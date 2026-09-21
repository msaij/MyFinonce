"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { AppShell } from "@/components/layout/AppShell";
import { StatCard } from "@/components/shared/StatCard";
import { Banner } from "@/components/shared/Banner";
import { StatusPill } from "@/components/layout/StatusPill";
import { formatDate } from "@/lib/format";
import { useLogStream } from "@/lib/hooks";
import { ApiError, getAdminToken, setAdminToken } from "@/lib/api/client";
import { getMetaStatus, getDataQuality } from "@/lib/api/meta";
import {
  getAdminStatus,
  triggerDailySync,
  triggerTerSync,
  recomputeSummary,
  startTerBackfill,
  stopTerBackfill,
  startHistoricalBackfill,
  stopHistoricalBackfill,
  getBackfillChunkCount,
  type SyncHistory,
} from "@/lib/api/admin";

const EARLIEST_BACKFILL_YEAR = 2000;

// Landing on "overview" (status/health at a glance), not the log -- an ops page should
// answer "is everything OK?" before it shows a scrolling feed of everything that happened.
const TABS = ["overview", "sync", "monitor", "reference"] as const;
type Tab = (typeof TABS)[number];
const TAB_LABELS: Record<Tab, string> = {
  overview: "Overview",
  sync: "Sync & Backfill",
  monitor: "Activity Log",
  reference: "Reference",
};

function epochToStr(seconds: number | null): string {
  if (!seconds) return "-";
  return new Date(seconds * 1000).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function SyncHealthCards({ title, caption, history, freshnessNote }: { title: string; caption: string; history: SyncHistory; freshnessNote?: React.ReactNode }) {
  return (
    <div className="mt-6">
      <h3 className="text-base font-bold">{title}</h3>
      <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
        {caption}
      </p>
      <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-3">
        {freshnessNote}
        <StatCard
          title="Last Sync Attempt"
          value={history.last_attempt_at ? formatDate(history.last_attempt_at) : "None yet"}
          sub={history.last_attempt_at ? `Trigger: ${history.last_attempt_trigger ?? "manual"}` : "Since this container started"}
        />
        <StatCard
          title="Last Successful Sync"
          value={history.last_success_at ? formatDate(history.last_success_at) : "None yet"}
          sub={history.last_success_msg ?? "Since this container started"}
          tone={history.last_success_at ? "pos" : "neutral"}
        />
        <StatCard
          title="Sync Attempts (This Session)"
          value={`${history.total_syncs}`}
          sub={history.total_failures ? `${history.total_failures} failed` : "0 failed"}
          tone={history.total_failures ? "warn" : "neutral"}
        />
      </div>
      {history.last_error && history.last_attempt_at === history.last_failure_at && (
        <div className="mt-3">
          <Banner level="warning">
            <b>Most recent sync attempt failed:</b> {history.last_error}. It will retry automatically on the next scheduled run.
          </Banner>
        </div>
      )}
    </div>
  );
}

export default function DataManagementPage() {
  const queryClient = useQueryClient();
  const { data: status, isFetching } = useQuery({
    queryKey: ["admin-status"],
    queryFn: getAdminStatus,
    refetchInterval: 5000,
  });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin-status"] });

  const [activeTab, setActiveTab] = useState<Tab>("overview");
  const [logLevel, setLogLevel] = useState<"INFO" | "WARNING" | "ERROR">("INFO");
  const [logFilter, setLogFilter] = useState("");
  const { entries: logEntries, connected: logConnected } = useLogStream(logLevel, logFilter);

  // Defaults to 2015, not the engine's own 2020 default -- matches what's actually wanted
  // for the very first backfill after a data reset, without forcing a re-type every time.
  const [backfillStartYear, setBackfillStartYear] = useState(2015);
  // TER backfill's "Since [year]" -- mirrors the NAV backfill's flexible start-year input
  // above instead of only offering fixed 12-months/FY2018-19 presets. Same default (2015)
  // and floor (EARLIEST_BACKFILL_YEAR, 2000) as the NAV engine now, by request ("same
  // flexibility as AMFI") -- AMFI's TER-disclosure portal itself only actually has data
  // back to FY2018-19 (still stated in the copy below), so a year before that just means
  // the older requested months come back empty, not an error; this input no longer
  // pretends the portal's own limit is this control's limit too.
  const [terStartYear, setTerStartYear] = useState(2015);
  const { data: chunkCountData } = useQuery({
    queryKey: ["backfill-chunk-count", backfillStartYear],
    queryFn: () => getBackfillChunkCount(backfillStartYear),
    enabled: backfillStartYear >= EARLIEST_BACKFILL_YEAR && backfillStartYear <= new Date().getFullYear(),
  });

  const dailySyncMut = useMutation({ mutationFn: triggerDailySync, onSuccess: invalidate });
  const terSyncMut = useMutation({ mutationFn: triggerTerSync, onSuccess: invalidate });
  const recomputeMut = useMutation({ mutationFn: recomputeSummary, onSuccess: invalidate });
  const startTerBackfillMut = useMutation({ mutationFn: (n: number) => startTerBackfill(n), onSuccess: invalidate });
  const stopTerBackfillMut = useMutation({ mutationFn: stopTerBackfill, onSuccess: invalidate });
  const startHistBackfillMut = useMutation({ mutationFn: (v: { year: number; max?: number; resume?: boolean }) => startHistoricalBackfill(v.year, v.max, v.resume ?? true), onSuccess: invalidate });
  const stopHistBackfillMut = useMutation({ mutationFn: stopHistoricalBackfill, onSuccess: invalidate });

  const stats = status?.stats;
  const cov = status?.cost_coverage;
  const total = Math.max(1, cov?.total_schemes ?? 1);
  // Same shape as the historical NAV engine's own chunk-count preview, just in months
  // instead of chunks -- there's no server-side "count the months" endpoint to call for
  // this (unlike NAV's chunk-count, which depends on chunk boundaries only the backend
  // computes), so this stays a client-side estimate exactly like the old fixed
  // FY2018-19 preset already was, just parameterized by year now instead of hardcoded.
  const monthsSinceTerYear = Math.max(1, (new Date().getFullYear() - terStartYear) * 12 + new Date().getMonth() + 1 + 9);
  // Durable, unlike backfill_status.records_added, which resets with every run.
  // Optional-chained because a database created before the checkpoint table existed
  // returns no progress block until the next /status call after init_db() runs.
  const completedChunks = status?.backfill_progress?.completed_chunks ?? 0;
  const { data: meta } = useQuery({ queryKey: ["meta-status"], queryFn: getMetaStatus });
  const { data: quality } = useQuery({ queryKey: ["data-quality"], queryFn: getDataQuality });
  const [adminTokenInput, setAdminTokenInput] = useState("");
  const authRequired = meta?.flags?.admin_auth_required === true;
  const tokenConfigured = meta?.flags?.admin_token_configured === true;

  return (
    <AppShell>
      <h1 className="mf-page-title">Data Management & AMFI Synchronization</h1>
      <p className="mf-page-caption">Monitor sync health, database storage, and official TER coverage -- and trigger on-demand sync from official AMFI portals.</p>
      {authRequired && !tokenConfigured && (
        <div className="mt-3">
          <Banner level="warning">Set ADMIN_TOKEN in compose. Writes fail closed until it is configured.</Banner>
        </div>
      )}
      {authRequired && (
        <div className="mt-3 flex items-end gap-2">
          <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
            Admin token (session only)
            <input
              type="password"
              value={adminTokenInput || getAdminToken() || ""}
              onChange={(e) => setAdminTokenInput(e.target.value)}
              className="rounded-lg border px-2 py-1.5 text-sm"
              style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
            />
          </label>
          <button
            type="button"
            className="rounded-lg px-3 py-2 text-xs font-semibold"
            style={{ background: "var(--mf-accent)", color: "white" }}
            onClick={() => setAdminToken(adminTokenInput)}
          >
            Store token
          </button>
        </div>
      )}

      <div className="mt-6 flex flex-wrap gap-1.5 border-b pb-2" style={{ borderColor: "var(--mf-border)" }}>
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setActiveTab(t)}
            className="rounded-full px-3 py-1.5 text-xs font-semibold"
            style={{ background: activeTab === t ? "var(--mf-accent-bg)" : "transparent", color: activeTab === t ? "var(--mf-accent)" : "var(--mf-fg)" }}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
        {isFetching && (
          <span className="ml-auto self-center text-xs" style={{ color: "var(--mf-muted)" }}>
            Refreshing...
          </span>
        )}
      </div>

      {activeTab === "overview" && status && stats && cov && (
        <div className="mt-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {status.staleness.is_stale ? (
              <StatCard title="NAV Freshness" value="Sync Pending" sub={`Expected through ${status.staleness.expected_date ? formatDate(status.staleness.expected_date) : "today"}`} tone="warn" />
            ) : (
              <StatCard title="NAV Freshness" value="Live" sub={`Synced through ${status.staleness.current_max_date ? formatDate(status.staleness.current_max_date) : "-"}`} tone="pos" />
            )}
            <StatCard title="Background Sync Daemon" value={status.enable_sync_daemon ? "Enabled" : "Disabled"} sub={status.enable_sync_daemon ? "Auto NAV + TER sync on schedule" : "Manual sync only, for now"} tone={status.enable_sync_daemon ? "pos" : "warn"} />
            <StatCard title="Database Storage" value={`${stats.file_size_mb} MB`} sub={`${stats.schemes_count.toLocaleString("en-IN")} schemes · ${stats.nav_count.toLocaleString("en-IN")} NAV records`} />
          </div>

          {(status.ter_backfill_status.is_running || status.backfill_status.is_running) && (
            <div className="mt-4">
              <Banner level="info">
                A backfill is running in the background --
                {status.backfill_status.is_running && ` NAV chunk ${status.backfill_status.current_chunk_idx}/${status.backfill_status.total_chunks}`}
                {status.backfill_status.is_running && status.ter_backfill_status.is_running && " and"}
                {status.ter_backfill_status.is_running && ` TER month ${status.ter_backfill_status.current_month_idx}/${status.ter_backfill_status.total_months}`}
                . See the Sync & Backfill tab for progress and stop controls.
              </Banner>
            </div>
          )}

          <h3 className="mt-8 text-base font-bold border-t pt-4" style={{ borderColor: "var(--mf-border)" }}>
            TER Coverage
          </h3>
          <p className="mt-1 text-xs" style={{ color: "var(--mf-muted)" }}>
            How many of {(cov.total_schemes ?? 0).toLocaleString("en-IN")} tracked schemes have a dated, sourced expense ratio -- see the Reference tab for what each status means.
          </p>
          <div className="mt-3 grid grid-cols-2 gap-4 md:grid-cols-3">
            <StatCard title="Official (AMFI TER Portal)" value={cov.ter_official.toLocaleString("en-IN")} sub={`${((cov.ter_official / total) * 100).toFixed(1)}% of schemes`} tone="pos" />
            <StatCard title="Legacy (Unverified)" value={cov.ter_legacy.toLocaleString("en-IN")} sub={`${((cov.ter_legacy / total) * 100).toFixed(1)}% of schemes`} tone="warn" />
            <StatCard title="Unknown" value={cov.ter_unknown.toLocaleString("en-IN")} sub={`${((cov.ter_unknown / total) * 100).toFixed(1)}% of schemes`} tone={cov.ter_unknown > 0 ? "neg" : "neutral"} />
          </div>
          {quality && (
            <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-3">
              <StatCard title="Official TER coverage" value={`${(quality.ter_official_coverage_ratio * 100).toFixed(1)}%`} sub={`${quality.ter_official_schemes} / ${quality.schemes_count}`} />
              <StatCard title="NAV gap rate" value={quality.nav_gap_rate != null ? quality.nav_gap_rate.toFixed(2) : "—"} sub="lag weeks vs today" />
              <StatCard title="Market proxy last NAV" value={quality.factor_market_last_nav ?? "—"} />
            </div>
          )}

          <SyncHealthCards
            title="NAV Sync Health"
            caption="Automated schedule: every night at 00:05 IST and 23:30 IST, plus an hourly heartbeat that catches up automatically if any run is missed or fails."
            history={status.sync_history}
          />
          <SyncHealthCards
            title="Official TER Sync Health"
            caption="A separate, daily sync against AMFI's own TER-disclosure portal -- different source, different schedule, tracked independently. Covers the current calendar month only on the automated schedule; see the Sync & Backfill tab for older months."
            history={status.ter_sync_history}
          />
        </div>
      )}

      {activeTab === "sync" && status && (
        <div className="mt-4">
          <h3 className="text-base font-bold">Manual Actions</h3>
          <div className="mt-2 grid grid-cols-1 gap-4 md:grid-cols-3">
            <div className="filter-box">
              <div className="text-sm font-semibold">Trigger Live AMFI Sync</div>
              <p className="mt-1 text-xs" style={{ color: "var(--mf-muted)" }}>
                Downloads the latest official closing NAV file from AMFI, updates the database, and re-materializes all performance metrics.
              </p>
              <button
                type="button"
                disabled={dailySyncMut.isPending}
                onClick={() => dailySyncMut.mutate()}
                className="mt-3 rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-50"
                style={{ background: "var(--mf-accent)", color: "white" }}
              >
                {dailySyncMut.isPending ? "Syncing..." : "Start Daily NAV Sync Now"}
              </button>
              {dailySyncMut.isSuccess && <p className="mt-2 text-xs" style={{ color: "var(--mf-success)" }}>{dailySyncMut.data.message}</p>}
              {dailySyncMut.isError && <p className="mt-2 text-xs" style={{ color: "var(--mf-danger)" }}>{(dailySyncMut.error as ApiError).message}</p>}
            </div>
            <div className="filter-box">
              <div className="text-sm font-semibold">Trigger Official TER Sync</div>
              <p className="mt-1 text-xs" style={{ color: "var(--mf-muted)" }}>
                Fetches this month&apos;s official TER disclosure from AMFI&apos;s TER portal and promotes matched schemes to &quot;official&quot; status.
              </p>
              <button
                type="button"
                disabled={terSyncMut.isPending}
                onClick={() => terSyncMut.mutate()}
                className="mt-3 rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-50"
                style={{ background: "var(--mf-accent)", color: "white" }}
              >
                {terSyncMut.isPending ? "Syncing..." : "Sync Official TER Now"}
              </button>
              {terSyncMut.isSuccess && <p className="mt-2 text-xs" style={{ color: "var(--mf-success)" }}>{terSyncMut.data.message}</p>}
              {terSyncMut.isError && <p className="mt-2 text-xs" style={{ color: "var(--mf-danger)" }}>{(terSyncMut.error as ApiError).message}</p>}
            </div>
            <div className="filter-box">
              <div className="text-sm font-semibold">Re-materialize Performance Table</div>
              <p className="mt-1 text-xs" style={{ color: "var(--mf-muted)" }}>
                Recomputes all 1D/7D/30D/90D/1Y returns, 52-week High/Low, and asset-class summaries across all {(stats?.schemes_count ?? 0).toLocaleString("en-IN")} schemes.
              </p>
              <button
                type="button"
                disabled={recomputeMut.isPending}
                onClick={() => recomputeMut.mutate()}
                className="mt-3 rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-50"
                style={{ background: "var(--mf-card-bg)", color: "var(--mf-fg)", border: "1px solid var(--mf-border)" }}
              >
                {recomputeMut.isPending ? "Recomputing..." : "Recompute Summary Table"}
              </button>
              {recomputeMut.isSuccess && <p className="mt-2 text-xs" style={{ color: "var(--mf-success)" }}>{recomputeMut.data.message}</p>}
            </div>
          </div>

          <h3 className="mt-8 text-base font-bold border-t pt-4" style={{ borderColor: "var(--mf-border)" }}>
            Historical TER Backfill
          </h3>
          <p className="mt-1 text-sm" style={{ color: "var(--mf-muted)" }}>
            The daily/startup TER sync only ever covers the current calendar month. AMFI&apos;s portal reaches back to FY2018-19 -- use this to pull in
            older months on demand, in the background. Pages of each month now fetch concurrently, so a busy ~20,000-row month takes roughly a sixth of
            the time a fully sequential fetch would.
          </p>
          {status.ter_backfill_status.is_running ? (
            <div className="mt-3">
              <Banner level={status.ter_backfill_status.should_stop ? "warning" : "info"}>
                {status.ter_backfill_status.should_stop ? (
                  <>
                    <b>Stopping</b> -- finishing month {status.ter_backfill_status.current_month_idx} of {status.ter_backfill_status.total_months} (
                    {status.ter_backfill_status.current_month_str}), then halting. This can take up to a minute or so on a large month.
                  </>
                ) : (
                  <>
                    TER Backfill In Progress: month {status.ter_backfill_status.current_month_idx} of {status.ter_backfill_status.total_months} (
                    {status.ter_backfill_status.current_month_str})
                  </>
                )}
              </Banner>
              {status.ter_backfill_status.last_error && <div className="mt-2"><Banner level="warning">Note: {status.ter_backfill_status.last_error}</Banner></div>}
              <button
                type="button"
                disabled={status.ter_backfill_status.should_stop || stopTerBackfillMut.isPending}
                onClick={() => stopTerBackfillMut.mutate()}
                className="mt-2 rounded-lg px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
                style={{ background: "var(--mf-danger)", color: "white" }}
              >
                {status.ter_backfill_status.should_stop ? "Stopping..." : "Stop TER Backfill"}
              </button>
            </div>
          ) : (
            <div className="mt-3 flex flex-wrap gap-2">
              {status.ter_backfill_status.finished_at && <Banner level="info">TER backfill session completed at {epochToStr(status.ter_backfill_status.finished_at)}.</Banner>}
              <button
                type="button"
                onClick={() => startTerBackfillMut.mutate(12)}
                className="rounded-lg px-4 py-2 text-sm font-semibold"
                style={{ background: "var(--mf-accent)", color: "white" }}
              >
                Backfill Last 12 Months of TER
              </button>
              <div className="flex items-center gap-2 rounded-lg border px-2 py-1" style={{ borderColor: "var(--mf-border)" }}>
                <label className="text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
                  Since
                </label>
                <input
                  type="number"
                  min={EARLIEST_BACKFILL_YEAR}
                  max={new Date().getFullYear()}
                  value={terStartYear}
                  onChange={(e) => setTerStartYear(Number(e.target.value))}
                  className="w-20 rounded border px-2 py-1 text-sm"
                  style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
                />
                <button
                  type="button"
                  onClick={() => startTerBackfillMut.mutate(monthsSinceTerYear)}
                  className="rounded-lg px-4 py-2 text-sm font-semibold"
                  style={{ background: "var(--mf-accent)", color: "white" }}
                >
                  Start TER Backfill Since {terStartYear} (~{monthsSinceTerYear} months)
                </button>
              </div>
            </div>
          )}
          {startTerBackfillMut.isError && <p className="mt-2 text-xs" style={{ color: "var(--mf-danger)" }}>{(startTerBackfillMut.error as ApiError).message}</p>}

          <h3 className="mt-8 text-base font-bold border-t pt-4" style={{ borderColor: "var(--mf-border)" }}>
            Multi-Year Historical Backfill Engine
          </h3>
          <p className="mt-1 text-sm" style={{ color: "var(--mf-muted)" }}>
            AMFI limits each individual HTTP download to 90 days at a time. This pipeline divides the timeline from a chosen start year to present into
            89-day chunks, downloads them in reverse-chronological order, and bulk-ingests them in the background -- pick how far back below.
          </p>
          {status.backfill_status.is_running ? (
            <div className="mt-3">
              <Banner level={status.backfill_status.should_stop ? "warning" : "info"}>
                {status.backfill_status.should_stop ? (
                  <>
                    <b>Stopping</b> -- finishing chunk {status.backfill_status.current_chunk_idx} of {status.backfill_status.total_chunks} (
                    {status.backfill_status.current_chunk_str}), then halting. {status.backfill_status.records_added.toLocaleString("en-IN")} NAV records
                    added so far this session.
                  </>
                ) : (
                  <>
                    Backfill In Progress: chunk {status.backfill_status.current_chunk_idx} of {status.backfill_status.total_chunks} (
                    {status.backfill_status.current_chunk_str}) -- {status.backfill_status.records_added.toLocaleString("en-IN")} NAV records added this
                    session.
                    {(status.backfill_status.skipped_chunks ?? 0) > 0 &&
                      ` Resumed: ${status.backfill_status.skipped_chunks} range(s) already complete were skipped.`}
                  </>
                )}
              </Banner>
              {status.backfill_status.last_error && <div className="mt-2"><Banner level="warning">Note: {status.backfill_status.last_error}</Banner></div>}
              <button
                type="button"
                disabled={status.backfill_status.should_stop || stopHistBackfillMut.isPending}
                onClick={() => stopHistBackfillMut.mutate()}
                className="mt-2 rounded-lg px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
                style={{ background: "var(--mf-danger)", color: "white" }}
              >
                {status.backfill_status.should_stop ? "Stopping..." : "Pause / Stop Backfill"}
              </button>
            </div>
          ) : (
            <div className="mt-3 flex flex-wrap gap-2">
              {status.backfill_status.finished_at && (
                <Banner level="info">
                  Backfill session completed at {epochToStr(status.backfill_status.finished_at)}! Ingested {status.backfill_status.records_added.toLocaleString("en-IN")} historical NAV records.
                </Banner>
              )}
              <button
                type="button"
                onClick={() => startHistBackfillMut.mutate({ year: 2025, max: 4 })}
                className="rounded-lg px-4 py-2 text-sm font-semibold"
                style={{ background: "var(--mf-card-bg)", color: "var(--mf-fg)", border: "1px solid var(--mf-border)" }}
              >
                Backfill Past 1 Year (4 Quarters)
              </button>
              <div className="flex items-center gap-2 rounded-lg border px-2 py-1" style={{ borderColor: "var(--mf-border)" }}>
                <label className="text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
                  Since
                </label>
                <input
                  type="number"
                  min={EARLIEST_BACKFILL_YEAR}
                  max={new Date().getFullYear()}
                  value={backfillStartYear}
                  onChange={(e) => setBackfillStartYear(Number(e.target.value))}
                  className="w-20 rounded border px-2 py-1 text-sm"
                  style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
                />
                <button
                  type="button"
                  disabled={!chunkCountData}
                  onClick={() => startHistBackfillMut.mutate({ year: backfillStartYear })}
                  className="rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-50"
                  style={{ background: "var(--mf-accent)", color: "white" }}
                >
                  Start Full Backfill Since {backfillStartYear} ({chunkCountData?.total_chunks ?? "..."} Chunks)
                </button>
              </div>
              {completedChunks > 0 && (
                <div className="w-full">
                  <Banner level="info">
                    {completedChunks.toLocaleString("en-IN")} date range{completedChunks === 1 ? "" : "s"} already ingested and
                    checkpointed. Starting a backfill resumes from there -- completed ranges are skipped, not re-downloaded.
                  </Banner>
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm(
                        `Forget all ${completedChunks} completed checkpoints and re-download every date range since ${backfillStartYear} from scratch?\n\n` +
                        "Only needed if you believe AMFI has restated past NAVs -- a normal backfill already corrects any changed value it finds."
                      )) {
                        startHistBackfillMut.mutate({ year: backfillStartYear, resume: false });
                      }
                    }}
                    className="mt-2 rounded-lg px-3 py-1.5 text-xs font-semibold"
                    style={{ background: "var(--mf-card-bg)", color: "var(--mf-fg)", border: "1px solid var(--mf-border)" }}
                  >
                    Re-download Everything (Ignore Checkpoints)
                  </button>
                </div>
              )}
            </div>
          )}
          {startHistBackfillMut.isError && <p className="mt-2 text-xs" style={{ color: "var(--mf-danger)" }}>{(startHistBackfillMut.error as ApiError).message}</p>}

        </div>
      )}

      {activeTab === "monitor" && (
        <div className="mt-4">
          <div className="flex items-center justify-between">
            <h3 className="text-base font-bold">Live Activity Log</h3>
            <StatusPill label={logConnected ? "Live" : "Connecting..."} level={logConnected ? "success" : "neutral"} />
          </div>
          <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
            A live push (Server-Sent Events) of what this app&apos;s background processes are doing right now -- NAV sync, TER sync, TER backfill, the
            multi-year NAV backfill, summary recompute, and anything else that logs.
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
              Minimum level
              <select
                value={logLevel}
                onChange={(e) => setLogLevel(e.target.value as never)}
                className="rounded-lg border px-2 py-1 text-sm"
                style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
              >
                <option>INFO</option>
                <option>WARNING</option>
                <option>ERROR</option>
              </select>
            </label>
            <label className="flex flex-1 flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)", minWidth: 200 }}>
              Filter (e.g. &quot;TER&quot;, &quot;backfill&quot;, &quot;nav&quot;)
              <input
                type="text"
                value={logFilter}
                onChange={(e) => setLogFilter(e.target.value)}
                className="rounded-lg border px-2 py-1 text-sm"
                style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
              />
            </label>
          </div>

          {logEntries.length === 0 ? (
            <p className="mt-3 text-sm" style={{ color: "var(--mf-muted)" }}>
              Nothing captured yet -- trigger a sync/backfill action from the Sync & Backfill tab, or wait for the next scheduled one, and activity will
              start appearing here.
            </p>
          ) : (
            <div className="mt-3 max-h-[420px] overflow-y-auto rounded-xl border p-2" style={{ borderColor: "var(--mf-border)" }}>
              {[...logEntries].reverse().map((e, i) => (
                <div
                  key={i}
                  className="flex items-baseline gap-2.5 border-b py-1.5 text-xs"
                  style={{ borderColor: "var(--mf-border)" }}
                >
                  <span className="flex-none font-mono" style={{ width: 64, color: "var(--mf-muted)" }}>
                    {new Date(e.time).toLocaleTimeString("en-IN", { hour12: false })}
                  </span>
                  <span className="flex-none" style={{ width: 74 }}>
                    <StatusPill label={e.level} level={e.level === "ERROR" || e.level === "CRITICAL" ? "danger" : e.level === "WARNING" ? "warning" : "neutral"} />
                  </span>
                  <span className="flex-none truncate" style={{ width: 120, color: "var(--mf-muted)" }} title={e.logger}>
                    {e.logger}
                  </span>
                  <span className="flex-1 break-words">{e.message}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === "reference" && status && stats && (
        <div className="mt-4">
          <h3 className="text-base font-bold">Database Storage</h3>
          <div className="mt-2 grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCard title="Database Storage Size" value={`${stats.file_size_mb} MB`} />
            <StatCard title="Total Mutual Funds" value={stats.schemes_count.toLocaleString("en-IN")} sub={`Across ${stats.amc_count} fund houses`} />
            <StatCard title="Historical NAV Records" value={stats.nav_count.toLocaleString("en-IN")} />
            <StatCard title="Historical Data Span" value={stats.max_date ? `${stats.min_date} to ${stats.max_date}` : "N/A"} />
          </div>

          <div className="mt-6 grid grid-cols-1 gap-6 md:grid-cols-2">
            <div>
              <h4 className="text-sm font-semibold">Data Sources</h4>
              <ul className="mt-2 flex flex-col gap-1.5 text-sm">
                <li>
                  <b>Daily Master NAV Feed</b>
                  <br />
                  <code className="text-xs">https://portal.amfiindia.com/spages/NAVAll.txt</code>
                </li>
                <li>
                  <b>90-Day Historical Reports</b>
                  <br />
                  <code className="text-xs">https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx</code>
                </li>
                <li>
                  <b>Official TER Disclosure Portal</b>
                  <br />
                  <code className="text-xs">{status.ter_portal_url}</code>
                </li>
              </ul>
            </div>
            <div>
              <h4 className="text-sm font-semibold">Storage & Scheduler Details</h4>
              <ul className="mt-2 flex flex-col gap-1.5 text-sm">
                <li>
                  <b>Database</b>: PostgreSQL 16 (<code className="text-xs">{stats.db_path}</code>)
                </li>
                <li>
                  <b>Background sync daemon</b>: {status.enable_sync_daemon ? "enabled" : "disabled"}
                </li>
                <li>
                  <b>Automated NAV Schedule</b>: every night at 00:05 IST and 23:30 IST, plus an hourly heartbeat catch-up.
                </li>
                <li>
                  <b>Automated TER Schedule</b>: on every container start, plus every night at 00:20 IST (current month only).
                </li>
                <li>
                  <b>Concurrency</b>: all sync/backfill writes are serialized behind a shared lock so a manual action can never race the
                  background daemon.
                </li>
              </ul>
            </div>
          </div>

          <h4 className="mt-6 text-sm font-semibold border-t pt-4" style={{ borderColor: "var(--mf-border)" }}>
            What each TER status means
          </h4>
          <table className="mt-2 w-full text-sm">
            <tbody>
              <tr className="border-b" style={{ borderColor: "var(--mf-border)" }}>
                <td className="py-1.5"><b>Official -- auto-synced</b></td>
                <td className="py-1.5" style={{ color: "var(--mf-muted)" }}>Matched by the AMFI TER-portal sync (see Overview tab); dated and sourced.</td>
              </tr>
              <tr className="border-b" style={{ borderColor: "var(--mf-border)" }}>
                <td className="py-1.5"><b>Legacy (unverified name match)</b></td>
                <td className="py-1.5" style={{ color: "var(--mf-muted)" }}>Matched only by scheme name against the bundled reference CSV -- no source URL or effective date on record.</td>
              </tr>
              <tr className="border-b" style={{ borderColor: "var(--mf-border)" }}>
                <td className="py-1.5"><b>Unknown</b></td>
                <td className="py-1.5" style={{ color: "var(--mf-muted)" }}>No TER record matched by any source yet.</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </AppShell>
  );
}
