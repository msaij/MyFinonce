"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";

import { AllocationPanel } from "@/components/holdings/AllocationPanel";
import { InsightsPanel } from "@/components/holdings/InsightsPanel";
import { PerformancePanel } from "@/components/holdings/PerformancePanel";
import { PlanningPanel } from "@/components/holdings/PlanningPanel";
import { PositionDrawer } from "@/components/holdings/PositionDrawer";
import { RiskPanel } from "@/components/holdings/RiskPanel";
import { TransactionDrawer } from "@/components/holdings/TransactionDrawer";
import { AppShell } from "@/components/layout/AppShell";
import { Banner } from "@/components/shared/Banner";
import { DataTable, type ColumnConfig } from "@/components/shared/DataTable";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { StatCard } from "@/components/shared/StatCard";
import {
  archivePortfolio,
  backupUrl,
  createPortfolio,
  deleteTransaction,
  exportCsvUrl,
  getHoldingsSummary,
  listPortfolios,
  listTransactions,
  restoreBackup,
  restoreTransaction,
  updatePortfolio,
  type PortfolioKey,
  type Position,
  type Transaction,
} from "@/lib/api/holdings";
import { formatDate, formatInr, formatSignedPct, toneOf } from "@/lib/format";
import { useUrlSync } from "@/lib/hooks";
import { TXN_TYPE_LABELS, formatSignedInr, formatUnits, parsePortfolioKey, positionBadges, xirrReason } from "@/lib/holdings";

const TABS = ["overview", "holdings", "allocation", "risk", "insights", "plan", "transactions"] as const;
type Tab = (typeof TABS)[number];
const DEFAULT_TAB: Tab = "overview";
const TAB_LABELS: Record<Tab, string> = {
  overview: "Performance",
  holdings: "Holdings",
  allocation: "Allocation",
  risk: "Risk",
  insights: "Insights & alerts",
  plan: "Goals & SIPs",
  transactions: "Transactions",
};

const btn: React.CSSProperties = { borderColor: "var(--mf-border)", color: "var(--mf-fg)" };

export default function HoldingsPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm" style={{ color: "var(--mf-muted)" }}>Loading holdings…</div>}>
      <HoldingsContent />
    </Suspense>
  );
}

function HoldingsContent() {
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const [pid, setPid] = useState<PortfolioKey>(() => parsePortfolioKey(searchParams.get("portfolio")) ?? "all");
  const [tab, setTab] = useState<Tab>(() => (TABS.includes(searchParams.get("tab") as Tab) ? (searchParams.get("tab") as Tab) : DEFAULT_TAB));
  const [showClosed, setShowClosed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<Transaction | null>(null);
  const [prefill, setPrefill] = useState<{ code: number; name: string } | null>(null);
  const [detailCode, setDetailCode] = useState<number | null>(null);
  const [toast, setToast] = useState<{ text: string; undoId?: number } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useUrlSync({ portfolio: pid === "all" ? "all" : pid, tab: tab !== DEFAULT_TAB ? tab : undefined });

  const portfoliosQ = useQuery({ queryKey: ["holdings", "portfolios"], queryFn: () => listPortfolios(true) });
  const portfolios = portfoliosQ.data ?? [];
  const active = portfolios.filter((p) => !p.archived);
  const current = pid === "all" ? null : portfolios.find((p) => p.id === pid) ?? null;

  // A deep link to a portfolio that no longer exists falls back to the household view.
  useEffect(() => {
    if (portfoliosQ.isSuccess && pid !== "all" && !portfolios.some((p) => p.id === pid)) setPid("all");
  }, [portfoliosQ.isSuccess, portfolios, pid]);

  const summaryQ = useQuery({
    queryKey: ["holdings", "summary", pid],
    queryFn: () => getHoldingsSummary(pid),
    enabled: portfoliosQ.isSuccess && (pid === "all" || portfolios.some((p) => p.id === pid)),
  });
  const txnsQ = useQuery({
    queryKey: ["holdings", "transactions", pid],
    queryFn: () => listTransactions(pid),
    enabled: tab === "transactions" && summaryQ.isSuccess,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["holdings"] });

  const del = useMutation({
    mutationFn: (id: number) => deleteTransaction(id),
    onSuccess: (res, id) => {
      invalidate();
      setToast({ text: res.deleted_ids.length > 1 ? "Switch (both legs) deleted." : "Transaction deleted.", undoId: id });
    },
    onError: (e: Error) => setActionError(e.message),
  });
  const undo = useMutation({
    mutationFn: (id: number) => restoreTransaction(id),
    onSuccess: () => {
      invalidate();
      setToast({ text: "Restored." });
    },
    onError: (e: Error) => setActionError(e.message),
  });

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), toast.undoId ? 10_000 : 3_000);
    return () => clearTimeout(t);
  }, [toast]);

  const openAdd = (scheme?: { code: number; name: string }) => {
    setEditing(null);
    setPrefill(scheme ?? null);
    setDrawerOpen(true);
  };

  const summary = summaryQ.data;
  const k = summary?.kpis;
  const positions = useMemo(
    () => (summary?.positions ?? []).filter((p) => showClosed || !p.is_closed),
    [summary, showClosed]
  );
  const closedCount = (summary?.positions ?? []).filter((p) => p.is_closed).length;
  const noPortfolios = portfoliosQ.isSuccess && active.length === 0;
  const emptyLedger = summary && summary.kpis.transactions === 0;

  const holdingsColumns: ColumnConfig[] = useMemo(
    () => [
      {
        key: "scheme_name",
        label: "Fund",
        render: (row) => {
          const p = row as unknown as Position;
          return (
            <div className="min-w-[16rem]">
              <button type="button" className="text-left font-semibold hover:underline" style={{ color: "var(--mf-accent)" }} onClick={() => setDetailCode(p.scheme_code)}>
                {p.scheme_name ?? p.scheme_code}
              </button>
              <div className="text-[0.7rem]" style={{ color: "var(--mf-muted)" }}>
                {[p.category, p.plan_type, p.option_type].filter(Boolean).join(" · ")}
              </div>
              {positionBadges(p).length > 0 && (
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {positionBadges(p).map((b) => (
                    <span key={b} className="rounded border px-1 text-[0.65rem] font-semibold" style={{ borderColor: "var(--mf-border)", color: b === "Stale NAV" || b === "Regular plan" ? "var(--mf-warning)" : "var(--mf-muted)" }}>
                      {b}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        },
      },
      { key: "units", label: "Units", sortValue: (r) => r.units, render: (_r, v) => formatUnits(v as number) },
      { key: "avg_cost_nav", label: "Avg cost NAV", format: "number", decimals: 4 },
      { key: "latest_nav", label: "NAV", render: (r, v) => (v == null ? "-" : <span title={`as of ${formatDate(r.latest_date as string)}`}>{Number(v).toFixed(4)}</span>) },
      { key: "cost_basis", label: "Invested", format: "inr" },
      { key: "current_value", label: "Value", format: "inr" },
      {
        key: "unrealised_gain",
        label: "Unrealised",
        sortValue: (r) => r.unrealised_gain,
        render: (r, v) => (
          <span className={toneOf(v as number) === "pos" ? "mf-pos" : toneOf(v as number) === "neg" ? "mf-neg" : ""}>
            {formatSignedInr(v as number)}
            <span className="block text-[0.7rem]">{formatSignedPct(r.unrealised_pct as number | null, 2)}</span>
          </span>
        ),
      },
      { key: "day_change", label: "1D ₹", sortValue: (r) => r.day_change, render: (_r, v) => <span className={toneOf(v as number) === "pos" ? "mf-pos" : toneOf(v as number) === "neg" ? "mf-neg" : ""}>{formatSignedInr(v as number)}</span> },
      {
        key: "xirr_pct",
        label: "XIRR",
        sortValue: (r) => r.xirr_pct,
        render: (r, v) => (v == null ? <span title={xirrReason(r.xirr_note as string | null)}>—</span> : formatSignedPct(v as number, 2)),
      },
      { key: "realised_gain", label: "Realised", render: (_r, v) => formatSignedInr(v as number) },
      { key: "weight_pct", label: "Weight", render: (_r, v) => (v == null ? "-" : `${(v as number).toFixed(1)}%`) },
      { key: "expense_ratio", label: "TER %", render: (r, v) => (v == null ? "-" : <span title={String(r.ter_status ?? "")}>{Number(v).toFixed(2)}</span>) },
    ],
    []
  );

  const txnColumns: ColumnConfig[] = useMemo(
    () => [
      { key: "trade_date", label: "Date", format: "date" },
      { key: "txn_type", label: "Type", render: (_r, v) => TXN_TYPE_LABELS[v as keyof typeof TXN_TYPE_LABELS] ?? String(v) },
      {
        key: "scheme_name",
        label: "Fund",
        render: (r) => (
          <button type="button" className="text-left hover:underline" style={{ color: "var(--mf-accent)" }} onClick={() => setDetailCode(r.scheme_code as number)}>
            {String(r.scheme_name ?? r.scheme_code)}
          </button>
        ),
      },
      ...(pid === "all"
        ? [{ key: "portfolio_id", label: "Portfolio", render: (_r: Record<string, unknown>, v: unknown) => portfolios.find((p) => p.id === v)?.name ?? String(v) } as ColumnConfig]
        : []),
      { key: "amount", label: "Amount", sortValue: (r) => Number(r.amount), render: (_r, v) => formatInr(Number(v)) },
      { key: "units", label: "Units", sortValue: (r) => Number(r.units), render: (_r, v) => formatUnits(v as string) },
      { key: "nav", label: "NAV", sortValue: (r) => Number(r.nav), render: (r, v) => `${Number(v).toFixed(4)}${r.nav_source === "user" ? " ✎" : ""}` },
      { key: "stamp_duty", label: "Stamp", render: (_r, v) => (Number(v) > 0 ? formatInr(Number(v)) : "-") },
      { key: "notes", label: "Notes", render: (_r, v) => <span className="text-xs">{(v as string) ?? ""}</span> },
      {
        key: "id",
        label: "",
        sortable: false,
        render: (r) => {
          const t = r as unknown as Transaction;
          return (
            <div className="flex gap-2 text-xs font-semibold">
              {!t.switch_group && (
                <button type="button" style={{ color: "var(--mf-accent)" }} onClick={() => { setEditing(t); setPrefill(null); setDrawerOpen(true); }}>
                  Edit
                </button>
              )}
              <button type="button" style={{ color: "var(--mf-danger)" }} disabled={del.isPending} onClick={() => { setActionError(null); del.mutate(t.id); }}>
                Delete
              </button>
            </div>
          );
        },
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pid, portfolios, del.isPending]
  );

  return (
    <AppShell pageContext={{ label: "Holdings", value: current?.name ?? "All portfolios", sub: k ? formatInr(k.current_value) : undefined }}>
      <h1 className="mf-page-title">Holdings</h1>
      <p className="mf-page-caption">
        Your own mutual fund portfolios, valued daily at official AMFI NAVs. Every figure traces back to a transaction you entered.
      </p>

      <PortfolioBar pid={pid} onSelect={setPid} />

      {actionError && (
        <div className="mt-3">
          <Banner level="danger">{actionError}</Banner>
        </div>
      )}

      {noPortfolios ? (
        <EmptyStart />
      ) : (
        <>
          {summaryQ.isError && (
            <div className="mt-4">
              <Banner level="danger">{(summaryQ.error as Error).message}</Banner>
            </div>
          )}

          {k && !emptyLedger && (
            <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
              <StatCard title="Current value" value={formatInr(k.current_value)} sub={summary?.as_of ? `NAVs as of ${formatDate(summary.as_of)}` : ""} />
              <StatCard
                title="Invested"
                value={formatInr(k.invested)}
                sub={`${k.open_positions} open holding${k.open_positions === 1 ? "" : "s"}`}
                tooltip={<FormulaTooltip label="Invested" description="Cost basis of the units you still hold (FIFO). Money from units already sold is in Realised gain." />}
              />
              <StatCard title="Unrealised gain" value={formatSignedInr(k.unrealised_gain)} tone={toneOf(k.unrealised_gain)} sub={formatSignedPct(k.unrealised_pct, 2)} subTone={toneOf(k.unrealised_pct)} />
              <StatCard
                title="Total gain"
                value={formatSignedInr(k.total_gain)}
                tone={toneOf(k.total_gain)}
                sub={`Realised ${formatSignedInr(k.realised_gain)} · Dividends ${formatInr(k.dividend_income)}`}
                tooltip={<FormulaTooltip label="Total gain" formula="unrealised + realised (FIFO) + dividends" />}
              />
              <StatCard
                title="XIRR"
                value={k.xirr_pct !== null ? formatSignedPct(k.xirr_pct, 2) : "—"}
                tone={toneOf(k.xirr_pct)}
                sub={k.xirr_pct === null ? xirrReason(k.xirr_note) : "Money-weighted, annualised"}
                tooltip={
                  <FormulaTooltip
                    label="XIRR"
                    formula="Σ CFᵢ / (1 + r)^(tᵢ / 365.25) = 0"
                    description="The annual rate that makes every rupee you put in and took out, plus today's value, net to zero. Switches between your own funds are internal and excluded."
                  />
                }
              />
              <StatCard title="1-day change" value={formatSignedInr(k.day_change)} tone={toneOf(k.day_change)} sub={formatSignedPct(k.day_change_pct, 2)} subTone={toneOf(k.day_change_pct)} />
            </div>
          )}

          <div className="mt-6 flex flex-wrap items-center gap-1.5 border-b pb-2" style={{ borderColor: "var(--mf-border)" }}>
            {TABS.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTab(t)}
                className="rounded-full px-3.5 py-1.5 text-xs font-bold"
                style={{
                  background: tab === t ? "var(--mf-accent)" : "transparent",
                  color: tab === t ? "#ffffff" : "var(--mf-fg)",
                  border: tab === t ? "1px solid var(--mf-accent)" : "1px solid var(--mf-border)",
                }}
              >
                {TAB_LABELS[t]}
              </button>
            ))}
            <div className="ml-auto flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => openAdd()}
                disabled={current?.archived}
                className="rounded-lg px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
                style={{ background: "var(--mf-accent)", color: "#fff" }}
              >
                + Add transaction
              </button>
              <a href={exportCsvUrl(pid)} className="rounded-lg border px-3 py-1.5 text-xs font-semibold" style={btn}>
                Export CSV
              </a>
              <a href={backupUrl} className="rounded-lg border px-3 py-1.5 text-xs font-semibold" style={btn} title="Every portfolio and transaction, for safe keeping">
                Download backup
              </a>
            </div>
          </div>

          {summaryQ.isLoading && <div className="mt-4 text-sm" style={{ color: "var(--mf-muted)" }}>Valuing your holdings…</div>}

          {emptyLedger && (
            <div className="mt-6 rounded-lg border p-6 text-center" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
              <div className="text-base font-bold">No transactions yet</div>
              <p className="mt-1 text-sm" style={{ color: "var(--mf-muted)" }}>
                Add your first purchase or SIP. Pick the fund and date. The NAV fills in from AMFI, and you can overwrite it with the one on your statement.
              </p>
              <button type="button" onClick={() => openAdd()} className="mt-3 rounded-lg px-4 py-2 text-sm font-semibold" style={{ background: "var(--mf-accent)", color: "#fff" }}>
                + Add your first holding
              </button>
            </div>
          )}

          {tab === "overview" && summary && !emptyLedger && <PerformancePanel pid={pid} />}

          {tab === "allocation" && summary && !emptyLedger && <AllocationPanel pid={pid} />}

          {tab === "risk" && summary && !emptyLedger && <RiskPanel pid={pid} />}

          {tab === "insights" && summary && !emptyLedger && <InsightsPanel pid={pid} portfolios={portfolios} onOpenScheme={setDetailCode} />}

          {tab === "plan" && summary && <PlanningPanel pid={pid} portfolios={portfolios} />}

          {tab === "holdings" && summary && !emptyLedger && (
            <div className="mt-4">
              {closedCount > 0 && (
                <label className="mb-2 flex items-center gap-2 text-xs" style={{ color: "var(--mf-muted)" }}>
                  <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} />
                  Show {closedCount} fully redeemed holding{closedCount === 1 ? "" : "s"}
                </label>
              )}
              <DataTable columns={holdingsColumns} rows={positions as unknown as Record<string, unknown>[]} keyField="scheme_code" />
            </div>
          )}

          {tab === "transactions" && !emptyLedger && (
            <div className="mt-4">
              {txnsQ.isLoading && <div className="text-sm" style={{ color: "var(--mf-muted)" }}>Loading ledger…</div>}
              {txnsQ.data && <DataTable columns={txnColumns} rows={[...txnsQ.data].reverse() as unknown as Record<string, unknown>[]} keyField="id" />}
            </div>
          )}

          <p className="mt-6 text-xs" style={{ color: "var(--mf-muted)" }}>
            Values use AMFI&apos;s closing NAV and ignore exit loads and taxes. Realised gain uses FIFO lots per portfolio as a performance measure, not a tax computation.
          </p>
        </>
      )}

      <TransactionDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        portfolios={portfolios}
        defaultPortfolioId={pid === "all" ? (active.length === 1 ? active[0].id : null) : pid}
        editing={editing}
        prefillScheme={prefill}
      />
      <PositionDrawer pid={pid} schemeCode={detailCode} onClose={() => setDetailCode(null)} />

      {toast && (
        <div className="fixed bottom-5 left-1/2 z-50 flex -translate-x-1/2 items-center gap-3 rounded-lg px-4 py-2 text-sm shadow-lg" role="status" style={{ background: "#0f172a", color: "#fff" }}>
          {toast.text}
          {toast.undoId && (
            <button type="button" className="font-bold underline" onClick={() => { undo.mutate(toast.undoId!); setToast(null); }}>
              Undo
            </button>
          )}
        </div>
      )}
    </AppShell>
  );
}

function PortfolioBar({ pid, onSelect }: { pid: PortfolioKey; onSelect: (p: PortfolioKey) => void }) {
  const queryClient = useQueryClient();
  const { data: portfolios = [] } = useQuery({ queryKey: ["holdings", "portfolios"], queryFn: () => listPortfolios(true) });
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);

  const current = pid === "all" ? null : portfolios.find((p) => p.id === pid) ?? null;
  const visible = portfolios.filter((p) => showArchived || !p.archived || p.id === pid);
  const done = () => {
    setCreating(false);
    setRenaming(false);
    setName("");
    setError(null);
    queryClient.invalidateQueries({ queryKey: ["holdings"] });
  };

  const create = useMutation({
    mutationFn: () => createPortfolio({ name: name.trim() }),
    onSuccess: (p) => {
      done();
      onSelect(p.id);
    },
    onError: (e: Error) => setError(e.message),
  });
  const rename = useMutation({ mutationFn: () => updatePortfolio(current!.id, { name: name.trim() }), onSuccess: done, onError: (e: Error) => setError(e.message) });
  const archive = useMutation({ mutationFn: () => archivePortfolio(current!.id), onSuccess: () => { done(); onSelect("all"); } });
  const unarchive = useMutation({ mutationFn: () => updatePortfolio(current!.id, { archived: false }), onSuccess: done });

  const pill = (active: boolean): React.CSSProperties => ({
    background: active ? "var(--mf-accent-bg)" : "transparent",
    color: active ? "var(--mf-accent)" : "var(--mf-fg)",
    border: `1px solid ${active ? "var(--mf-accent)" : "var(--mf-border)"}`,
  });

  return (
    <div className="mt-4 flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Portfolio">
        <button type="button" role="tab" aria-selected={pid === "all"} onClick={() => onSelect("all")} className="rounded-full px-3 py-1 text-xs font-semibold" style={pill(pid === "all")}>
          All portfolios
        </button>
        {visible.map((p) => (
          <button key={p.id} type="button" role="tab" aria-selected={pid === p.id} onClick={() => onSelect(p.id)} className="rounded-full px-3 py-1 text-xs font-semibold" style={{ ...pill(pid === p.id), opacity: p.archived ? 0.6 : 1 }}>
            {p.name}
            {p.archived ? " (archived)" : ""}
          </button>
        ))}
        {!creating && (
          <button type="button" onClick={() => { setCreating(true); setRenaming(false); setName(""); }} className="rounded-full px-3 py-1 text-xs font-semibold" style={{ color: "var(--mf-accent)", border: "1px dashed var(--mf-border)" }}>
            + New portfolio
          </button>
        )}
        {portfolios.some((p) => p.archived) && (
          <label className="ml-1 flex items-center gap-1 text-[0.7rem]" style={{ color: "var(--mf-muted)" }}>
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> archived
          </label>
        )}
        {current && !creating && !renaming && (
          <div className="ml-auto flex gap-3 text-xs font-semibold">
            <button type="button" style={{ color: "var(--mf-accent)" }} onClick={() => { setRenaming(true); setName(current.name); }}>
              Rename
            </button>
            {current.archived ? (
              <button type="button" style={{ color: "var(--mf-accent)" }} onClick={() => unarchive.mutate()}>
                Unarchive
              </button>
            ) : (
              <button type="button" style={{ color: "var(--mf-muted)" }} onClick={() => archive.mutate()} title="Hides it from the household view. Nothing is deleted.">
                Archive
              </button>
            )}
          </div>
        )}
      </div>
      {(creating || renaming) && (
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!name.trim()) return;
            (creating ? create : rename).mutate();
          }}
        >
          <input
            autoFocus
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
            placeholder={creating ? "e.g. Self, Spouse, Retirement" : ""}
            className="rounded-lg border px-2 py-1.5 text-sm"
            style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-fg)" }}
          />
          <button type="submit" className="rounded-lg px-3 py-1.5 text-xs font-semibold" style={{ background: "var(--mf-accent)", color: "#fff" }}>
            {creating ? "Create" : "Save"}
          </button>
          <button type="button" className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }} onClick={() => { setCreating(false); setRenaming(false); setError(null); }}>
            Cancel
          </button>
          {error && <span className="text-xs" style={{ color: "var(--mf-danger)" }}>{error}</span>}
        </form>
      )}
    </div>
  );
}

function EmptyStart() {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [msg, setMsg] = useState<{ level: "info" | "danger"; text: string } | null>(null);
  const restore = useMutation({
    mutationFn: async (file: File) => restoreBackup(JSON.parse(await file.text())),
    onSuccess: (r) => {
      setMsg({ level: "info", text: `Restored ${r.portfolios} portfolio(s) and ${r.holding_transactions} transaction(s).` });
      queryClient.invalidateQueries({ queryKey: ["holdings"] });
    },
    onError: (e: Error) => setMsg({ level: "danger", text: e.message }),
  });
  return (
    <div className="mt-6 rounded-lg border p-6" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
      <div className="text-base font-bold">Start tracking what you own</div>
      <p className="mt-1 text-sm" style={{ color: "var(--mf-muted)" }}>
        Create a portfolio above, one per person or goal (Self, Spouse, Retirement…). &quot;All portfolios&quot; then shows the whole household together.
      </p>
      <div className="mt-4 text-xs" style={{ color: "var(--mf-muted)" }}>
        Have a backup file from this app?{" "}
        <button type="button" className="font-semibold underline" style={{ color: "var(--mf-accent)" }} onClick={() => fileRef.current?.click()}>
          Restore it
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) restore.mutate(f);
            e.target.value = "";
          }}
        />
      </div>
      {msg && (
        <div className="mt-3">
          <Banner level={msg.level}>{msg.text}</Banner>
        </div>
      )}
    </div>
  );
}
