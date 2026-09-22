"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { Banner } from "@/components/shared/Banner";
import { DataTable, type ColumnConfig } from "@/components/shared/DataTable";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { StatCard } from "@/components/shared/StatCard";
import { getAllocation, getRebalance, putTargets, type AllocationBucket, type PortfolioKey } from "@/lib/api/holdings";
import { formatInr } from "@/lib/format";
import { parseNumber } from "@/lib/holdings";
import { AXIS, REFERENCE, SERIES_1 } from "@/lib/holdingsChart";

const inputStyle: React.CSSProperties = { borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-fg)" };

/** Top N buckets, the rest folded into "Other" (never an extra generated colour). */
function topN(rows: AllocationBucket[], n: number): AllocationBucket[] {
  if (rows.length <= n) return rows;
  const head = rows.slice(0, n);
  const tail = rows.slice(n);
  return [...head, { bucket: `Other (${tail.length})`, value: tail.reduce((a, r) => a + r.value, 0), weight_pct: tail.reduce((a, r) => a + r.weight_pct, 0) }];
}

function barFigure(rows: AllocationBucket[], targets?: Record<string, number> | null) {
  const ordered = [...rows].reverse();
  const labels = ordered.map((r) => (r.bucket.length > 44 ? `${r.bucket.slice(0, 43)}…` : r.bucket));
  const data: Record<string, unknown>[] = [
    {
      type: "bar", orientation: "h", name: "Actual", y: labels, x: ordered.map((r) => r.weight_pct),
      marker: { color: SERIES_1 }, text: ordered.map((r) => `${r.weight_pct.toFixed(1)}%`), textposition: "outside", cliponaxis: false,
      customdata: ordered.map((r) => r.value), hovertemplate: "%{y}<br>%{x:.1f}% · ₹%{customdata:,.0f}<extra></extra>",
    },
  ];
  if (targets) {
    data.push({
      type: "scatter", mode: "markers", name: "Target", y: labels, x: ordered.map((r) => targets[r.bucket] ?? 0),
      marker: { symbol: "line-ns-open", size: 22, color: REFERENCE, line: { width: 3, color: REFERENCE } },
      hovertemplate: "Target %{x:.1f}%<extra></extra>",
    });
  }
  return {
    data,
    layout: {
      height: Math.max(160, 50 + ordered.length * 34), margin: { l: 10, r: 60, t: targets ? 30 : 10 }, bargap: 0.35,
      showlegend: !!targets, legend: { orientation: "h", y: 1.15 },
      yaxis: { automargin: true }, xaxis: { ...AXIS, ticksuffix: "%", rangemode: "tozero" },
    },
  };
}

export function AllocationPanel({ pid }: { pid: PortfolioKey }) {
  const { data, isLoading, isError, error } = useQuery({ queryKey: ["holdings", "allocation", pid], queryFn: () => getAllocation(pid) });

  if (isLoading) return <div className="mt-4 text-sm" style={{ color: "var(--mf-muted)" }}>Grouping your holdings…</div>;
  if (isError) return <div className="mt-4"><Banner level="danger">{(error as Error).message}</Banner></div>;
  if (!data) return null;
  if (data.total_value <= 0) return <div className="mt-4 text-sm" style={{ color: "var(--mf-muted)" }}>Nothing is held right now.</div>;

  const c = data.concentration;
  const planRows = data.by_plan;
  const regular = planRows.find((r) => r.bucket.toLowerCase().includes("regular"));

  return (
    <div className="mt-4 flex flex-col gap-6">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard title="Funds held" value={String(c.fund_count)} />
        <StatCard
          title="Effective number of funds"
          value={c.effective_funds ? c.effective_funds.toFixed(1) : "-"}
          sub={c.hhi !== null ? `HHI ${c.hhi.toFixed(3)}` : ""}
          tooltip={<FormulaTooltip label="Effective number of funds" formula="1 / Σ wᵢ²" description="How many equal-sized funds your portfolio behaves like. 10 funds where one is 80% count as barely 1.5." />}
        />
        <StatCard title="Largest holding" value={c.top1_pct !== null ? `${c.top1_pct.toFixed(1)}%` : "-"} tone={c.top1_pct !== null && c.top1_pct > 40 ? "warn" : ""} />
        <StatCard title="Top 3 holdings" value={c.top3_pct !== null ? `${c.top3_pct.toFixed(1)}%` : "-"} />
      </div>

      {regular && (
        <Banner level="warning">
          {regular.weight_pct.toFixed(1)}% of this portfolio ({formatInr(regular.value)}) is in Regular plans, which carry a distributor commission inside the expense ratio.
          The Insights tab prices the gap fund by fund against each Direct twin&apos;s official TER.
        </Banner>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <section>
          <h3 className="flex items-center gap-1 text-base font-bold">
            Asset allocation
            <FormulaTooltip label="How funds are classified" description="From each scheme's SEBI category and name: index funds and ETFs are split into equity or debt by what they track, gold/silver ETFs are Gold & Commodities, overseas FoFs are International. Hybrid funds stay Hybrid, because their equity/debt split needs monthly portfolio disclosures." />
          </h3>
          <PlotlyChart figure={barFigure(data.by_asset_class, data.targets)} />
        </section>
        <section>
          <h3 className="text-base font-bold">By fund house</h3>
          <PlotlyChart figure={barFigure(topN(data.by_amc, 8))} />
        </section>
        <section>
          <h3 className="text-base font-bold">By SEBI category</h3>
          <PlotlyChart figure={barFigure(topN(data.by_category, 10))} />
        </section>
        <section>
          <h3 className="text-base font-bold">Plan and option</h3>
          <div className="grid grid-cols-2 gap-3">
            {[...data.by_plan, ...data.by_option].map((r) => (
              <StatCard key={r.bucket + r.value} title={r.bucket} value={`${r.weight_pct.toFixed(1)}%`} sub={formatInr(r.value)} />
            ))}
          </div>
        </section>
      </div>

      {pid === "all" ? (
        <Banner level="info">Targets and rebalancing are set per portfolio. Pick one above to set a target mix.</Banner>
      ) : (
        <TargetsSection portfolioId={pid} assetClasses={data.asset_classes} targets={data.targets} drift={data.drift} band={data.drift_band_pct} />
      )}
    </div>
  );
}

function TargetsSection({
  portfolioId,
  assetClasses,
  targets,
  drift,
  band,
}: {
  portfolioId: number;
  assetClasses: string[];
  targets: Record<string, number> | null;
  drift: import("@/lib/api/holdings").DriftRow[] | null;
  band: number;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(!targets);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [newMoney, setNewMoney] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setDraft(Object.fromEntries(assetClasses.map((a) => [a, targets?.[a] ? String(targets[a]) : ""])));
    setEditing(!targets);
  }, [targets, assetClasses]);

  const total = useMemo(() => Object.values(draft).reduce((a, v) => a + (parseNumber(v) ?? 0), 0), [draft]);
  const save = useMutation({
    mutationFn: () => putTargets(portfolioId, Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, parseNumber(v) ?? 0]))),
    onSuccess: () => {
      setErr(null);
      queryClient.invalidateQueries({ queryKey: ["holdings"] });
    },
    onError: (e: Error) => setErr(e.message),
  });
  const money = parseNumber(newMoney);
  const rebalance = useQuery({
    queryKey: ["holdings", "rebalance", portfolioId, money],
    queryFn: () => getRebalance(portfolioId, money!),
    enabled: !!targets && money !== null && money > 0,
  });

  const driftColumns: ColumnConfig[] = [
    { key: "asset_class", label: "Asset class" },
    { key: "actual_pct", label: "Actual", render: (_r, v) => `${(v as number).toFixed(1)}%` },
    { key: "target_pct", label: "Target", render: (_r, v) => `${(v as number).toFixed(1)}%` },
    { key: "drift_pct", label: "Drift", render: (_r, v) => `${(v as number) > 0 ? "+" : (v as number) < 0 ? "−" : ""}${Math.abs(v as number).toFixed(1)} pp` },
    {
      key: "status",
      label: `Status (±${band} pp band)`,
      render: (_r, v) =>
        v === "ok" ? <span>✓ Within band</span> : v === "over" ? <span className="mf-neg">▲ Over-weight</span> : <span className="mf-neg">▼ Under-weight</span>,
    },
  ];
  const rebalanceColumns: ColumnConfig[] = [
    { key: "asset_class", label: "Asset class" },
    { key: "amount", label: "Put in", format: "inr" },
    { key: "before_pct", label: "Now", render: (_r, v) => `${(v as number).toFixed(1)}%` },
    { key: "after_pct", label: "After", render: (_r, v) => `${(v as number).toFixed(1)}%` },
    { key: "target_pct", label: "Target", render: (_r, v) => `${(v as number).toFixed(1)}%` },
    { key: "suggested_scheme_name", label: "Suggested (your existing fund)", render: (r, v) => (r.amount as number) > 0 ? (v ? String(v) : <i>No fund held in this class yet; pick one</i>) : "-" },
  ];

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-bold">Target mix and drift</h3>
        {targets && !editing && (
          <button type="button" className="text-xs font-semibold" style={{ color: "var(--mf-accent)" }} onClick={() => setEditing(true)}>
            Edit targets
          </button>
        )}
      </div>

      {editing && (
        <form
          className="rounded-lg border p-3"
          style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            {assetClasses.map((a) => (
              <label key={a} className="flex flex-col gap-1 text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>
                {a}
                <input inputMode="decimal" className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={draft[a] ?? ""} placeholder="0" onChange={(e) => setDraft((d) => ({ ...d, [a]: e.target.value }))} />
              </label>
            ))}
          </div>
          <div className="mt-3 flex items-center gap-3 text-sm">
            <span className={Math.abs(total - 100) < 0.01 ? "" : "mf-neg"}>
              Total {total.toFixed(1)}% {Math.abs(total - 100) < 0.01 ? "✓" : "(must be 100%)"}
            </span>
            <button type="submit" disabled={Math.abs(total - 100) >= 0.01 || save.isPending} className="rounded-lg px-3 py-1.5 text-xs font-semibold disabled:opacity-50" style={{ background: "var(--mf-accent)", color: "#fff" }}>
              Save targets
            </button>
            {targets && (
              <button type="button" className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }} onClick={() => setEditing(false)}>
                Cancel
              </button>
            )}
          </div>
          {err && <div className="mt-2"><Banner level="danger">{err}</Banner></div>}
        </form>
      )}

      {drift && <DataTable columns={driftColumns} rows={drift as unknown as Record<string, unknown>[]} keyField="asset_class" />}

      {targets && (
        <div className="flex flex-col gap-2">
          <label className="flex items-center gap-2 text-sm font-semibold">
            Investing new money? How much:
            <input inputMode="decimal" className="w-40 rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={newMoney} placeholder="e.g. 50000" onChange={(e) => setNewMoney(e.target.value)} />
          </label>
          <p className="text-xs" style={{ color: "var(--mf-muted)" }}>Uses only new money and never suggests selling. Selling could trigger exit loads and taxes.</p>
          {rebalance.data && <DataTable columns={rebalanceColumns} rows={rebalance.data.rows as unknown as Record<string, unknown>[]} keyField="asset_class" />}
          {rebalance.isError && <Banner level="danger">{(rebalance.error as Error).message}</Banner>}
        </div>
      )}
    </section>
  );
}
