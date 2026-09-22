"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { Banner } from "@/components/shared/Banner";
import { DataTable, type ColumnConfig } from "@/components/shared/DataTable";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { SearchCombobox } from "@/components/shared/SearchCombobox";
import { StatCard } from "@/components/shared/StatCard";
import {
  createSipMandate,
  generateInstalments,
  getGoalStatus,
  listGoals,
  listSipMandates,
  saveGoal,
  updateSipMandate,
  type GenerateResult,
  type Goal,
  type Portfolio,
  type PortfolioKey,
  type SipMandate,
} from "@/lib/api/holdings";
import { formatDate, formatInr } from "@/lib/format";
import { useDebouncedValue } from "@/lib/hooks";
import { formatUnits, parseNumber, todayIso } from "@/lib/holdings";
import { AXIS, REFERENCE, SERIES_1 } from "@/lib/holdingsChart";

const inputStyle: React.CSSProperties = { borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-fg)" };
const label = "flex flex-col gap-1 text-xs font-semibold";

export function PlanningPanel({ pid, portfolios }: { pid: PortfolioKey; portfolios: Portfolio[] }) {
  return (
    <div className="mt-4 flex flex-col gap-10">
      <GoalsSection portfolios={portfolios} />
      <SipSection pid={pid} portfolios={portfolios} />
    </div>
  );
}

// --- SIP mandates ------------------------------------------------------------------------

function SipSection({ pid, portfolios }: { pid: PortfolioKey; portfolios: Portfolio[] }) {
  const queryClient = useQueryClient();
  const { data: mandates = [] } = useQuery({ queryKey: ["holdings", "sips", pid], queryFn: () => listSipMandates(pid) });
  const [adding, setAdding] = useState(false);
  const [preview, setPreview] = useState<GenerateResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["holdings"] });

  const gen = useMutation({
    mutationFn: ({ id, confirm }: { id: number; confirm: boolean }) => generateInstalments(id, confirm),
    onSuccess: (r) => {
      setErr(null);
      if (r.written) {
        setPreview(null);
        refresh();
      } else setPreview(r);
    },
    onError: (e: Error) => setErr(e.message),
  });
  const toggle = useMutation({ mutationFn: (m: SipMandate) => updateSipMandate(m.id, { active: !m.active }), onSuccess: refresh });
  const pname = (id: number) => portfolios.find((p) => p.id === id)?.name ?? `#${id}`;

  const previewCols: ColumnConfig[] = [
    { key: "trade_date", label: "Allotment date", format: "date" },
    { key: "amount", label: "Amount", render: (_r, v) => formatInr(Number(v)) },
    { key: "nav", label: "NAV (AMFI)", render: (_r, v) => Number(v).toFixed(4) },
    { key: "units", label: "Units", render: (_r, v) => formatUnits(v as string) },
    { key: "stamp_duty", label: "Stamp", render: (_r, v) => formatInr(Number(v)) },
  ];

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-bold">SIP mandates</h3>
          <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
            Describe a SIP once. We then write each past instalment for you at the real AMFI NAV of its allotment date. You preview them first, and nothing is written until you confirm.
          </p>
        </div>
        {!adding && (
          <button type="button" className="rounded-lg px-3 py-1.5 text-xs font-semibold" style={{ background: "var(--mf-accent)", color: "#fff" }} onClick={() => setAdding(true)}>
            + New SIP
          </button>
        )}
      </div>

      {adding && <SipForm portfolios={portfolios} defaultPortfolio={pid === "all" ? null : pid} onDone={() => { setAdding(false); refresh(); }} />}
      {err && <Banner level="danger">{err}</Banner>}

      {mandates.length === 0 && !adding && <div className="text-sm" style={{ color: "var(--mf-muted)" }}>No SIPs recorded for this view.</div>}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {mandates.map((m) => (
          <div key={m.id} className="metric-card" style={{ opacity: m.active ? 1 : 0.65 }}>
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="font-semibold">{m.scheme_name ?? `Scheme ${m.scheme_code}`}</div>
                <div className="text-xs" style={{ color: "var(--mf-muted)" }}>
                  {formatInr(m.current_amount)} on day {m.day_of_month} · {pname(m.portfolio_id)}
                  {Number(m.step_up_pct) > 0 ? ` · +${Number(m.step_up_pct)}%/yr` : ""}
                  {!m.active ? " · paused" : ""}
                </div>
                <div className="text-xs" style={{ color: "var(--mf-muted)" }}>
                  Since {formatDate(m.start_date)}{m.end_date ? ` until ${formatDate(m.end_date)}` : ""} · {m.instalments_recorded} recorded
                  {m.upcoming.length > 0 ? ` · next ${formatDate(m.upcoming[0].date)}` : ""}
                </div>
              </div>
              <button type="button" className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }} onClick={() => toggle.mutate(m)}>
                {m.active ? "Pause" : "Resume"}
              </button>
            </div>
            {m.instalments_pending > 0 && (
              <button type="button" className="mt-2 rounded-lg border px-3 py-1.5 text-xs font-semibold" style={{ borderColor: "var(--mf-accent)", color: "var(--mf-accent)" }} onClick={() => gen.mutate({ id: m.id, confirm: false })}>
                Review {m.instalments_pending} pending instalment{m.instalments_pending === 1 ? "" : "s"}
              </button>
            )}
          </div>
        ))}
      </div>

      {preview && (
        <div className="rounded-lg border p-3" style={{ borderColor: "var(--mf-accent)" }}>
          <div className="mb-2 text-sm font-bold">
            {preview.count} instalment{preview.count === 1 ? "" : "s"} · {formatInr(preview.total_amount)} will be added to your ledger
          </div>
          {preview.count > 0 && <DataTable columns={previewCols} rows={preview.rows as unknown as Record<string, unknown>[]} keyField="trade_date" />}
          {preview.skipped.length > 0 && <div className="mt-1 text-xs" style={{ color: "var(--mf-muted)" }}>{preview.skipped.length} skipped: NAV not published yet.</div>}
          <div className="mt-3 flex gap-2">
            <button type="button" disabled={preview.count === 0 || gen.isPending} className="rounded-lg px-3 py-1.5 text-xs font-semibold disabled:opacity-50" style={{ background: "var(--mf-accent)", color: "#fff" }} onClick={() => gen.mutate({ id: preview.mandate_id, confirm: true })}>
              Add {preview.count} to ledger
            </button>
            <button type="button" className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }} onClick={() => setPreview(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function SipForm({ portfolios, defaultPortfolio, onDone }: { portfolios: Portfolio[]; defaultPortfolio: number | null; onDone: () => void }) {
  const active = portfolios.filter((p) => !p.archived);
  const [portfolioId, setPortfolioId] = useState<number | null>(defaultPortfolio ?? (active.length === 1 ? active[0].id : null));
  const [scheme, setScheme] = useState<{ code: number; name: string } | null>(null);
  const [amount, setAmount] = useState("");
  const [day, setDay] = useState("5");
  const [start, setStart] = useState(todayIso());
  const [end, setEnd] = useState("");
  const [stepUp, setStepUp] = useState("0");
  const [err, setErr] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      createSipMandate({
        portfolio_id: portfolioId!,
        scheme_code: scheme!.code,
        amount: parseNumber(amount)!,
        day_of_month: Number(day),
        start_date: start,
        end_date: end || null,
        step_up_pct: parseNumber(stepUp) ?? 0,
      }),
    onSuccess: onDone,
    onError: (e: Error) => setErr(e.message),
  });
  const ok = portfolioId !== null && scheme && (parseNumber(amount) ?? 0) > 0 && start;
  return (
    <form className="rounded-lg border p-3" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }} onSubmit={(e) => { e.preventDefault(); if (ok) save.mutate(); }}>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        <label className={label} style={{ color: "var(--mf-muted)" }}>
          Portfolio
          <select className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={portfolioId ?? ""} onChange={(e) => setPortfolioId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">Choose…</option>
            {active.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <div className={`${label} md:col-span-2`} style={{ color: "var(--mf-muted)" }}>
          Fund
          {scheme ? (
            <div className="flex items-center justify-between rounded-lg border px-2 py-1.5 text-sm font-normal" style={inputStyle}>
              <span className="truncate">{scheme.name}</span>
              <button type="button" className="text-xs font-semibold" style={{ color: "var(--mf-accent)" }} onClick={() => setScheme(null)}>Change</button>
            </div>
          ) : (
            <SearchCombobox onSelect={(s) => setScheme({ code: s.scheme_code, name: s.display_label ?? s.scheme_name })} />
          )}
        </div>
        <label className={label} style={{ color: "var(--mf-muted)" }}>
          Monthly amount (₹)
          <input inputMode="decimal" className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="e.g. 10000" />
        </label>
        <label className={label} style={{ color: "var(--mf-muted)" }}>
          Debit day (1–28)
          <input type="number" min={1} max={28} className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={day} onChange={(e) => setDay(e.target.value)} />
        </label>
        <label className={label} style={{ color: "var(--mf-muted)" }}>
          Annual step-up (%)
          <input inputMode="decimal" className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={stepUp} onChange={(e) => setStepUp(e.target.value)} />
        </label>
        <label className={label} style={{ color: "var(--mf-muted)" }}>
          Started
          <input type="date" className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className={label} style={{ color: "var(--mf-muted)" }}>
          Ends (optional)
          <input type="date" className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <button type="submit" disabled={!ok || save.isPending} className="rounded-lg px-3 py-1.5 text-xs font-semibold disabled:opacity-50" style={{ background: "var(--mf-accent)", color: "#fff" }}>
          Save SIP
        </button>
        <button type="button" className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }} onClick={onDone}>Cancel</button>
        {err && <span className="text-xs" style={{ color: "var(--mf-danger)" }}>{err}</span>}
      </div>
    </form>
  );
}

// --- Goals --------------------------------------------------------------------------------

function GoalsSection({ portfolios }: { portfolios: Portfolio[] }) {
  const { data: goals = [] } = useQuery({ queryKey: ["holdings", "goals"], queryFn: listGoals });
  const [editing, setEditing] = useState<Goal | "new" | null>(null);
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-bold">Goals</h3>
          <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
            Link one or more portfolios to a goal. Each projection runs 2,000 market paths from the linked funds&apos; own behaviour, including your active SIPs.
          </p>
        </div>
        {editing === null && (
          <button type="button" className="rounded-lg px-3 py-1.5 text-xs font-semibold" style={{ background: "var(--mf-accent)", color: "#fff" }} onClick={() => setEditing("new")}>
            + New goal
          </button>
        )}
      </div>
      {editing !== null && <GoalForm goal={editing === "new" ? null : editing} portfolios={portfolios} onDone={() => setEditing(null)} />}
      {goals.length === 0 && editing === null && <div className="text-sm" style={{ color: "var(--mf-muted)" }}>No goals yet: retirement, a house, a child&apos;s education…</div>}
      {goals.map((g) => (
        <GoalCard key={g.id} goal={g} portfolios={portfolios} onEdit={() => setEditing(g)} />
      ))}
    </section>
  );
}

function GoalForm({ goal, portfolios, onDone }: { goal: Goal | null; portfolios: Portfolio[]; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(goal?.name ?? "");
  const [amount, setAmount] = useState(goal ? String(Number(goal.target_amount)) : "");
  const [date, setDate] = useState(goal?.target_date ?? "");
  const [infl, setInfl] = useState(goal ? String(Number(goal.inflation_pct)) : "6");
  const [linked, setLinked] = useState<number[]>(goal?.portfolio_ids ?? []);
  const [err, setErr] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (archived: boolean) =>
      saveGoal(goal?.id ?? null, { name, target_amount: parseNumber(amount)!, target_date: date, inflation_pct: parseNumber(infl) ?? 6, portfolio_ids: linked, archived }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["holdings"] });
      onDone();
    },
    onError: (e: Error) => setErr(e.message),
  });
  const ok = name.trim() && (parseNumber(amount) ?? 0) > 0 && date;
  return (
    <form className="rounded-lg border p-3" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }} onSubmit={(e) => { e.preventDefault(); if (ok) save.mutate(false); }}>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
        <label className={label} style={{ color: "var(--mf-muted)" }}>
          Goal
          <input className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="e.g. Retirement" />
        </label>
        <label className={label} style={{ color: "var(--mf-muted)" }}>
          Amount needed, in today&apos;s ₹
          <input inputMode="decimal" className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={amount} onChange={(e) => setAmount(e.target.value)} />
        </label>
        <label className={label} style={{ color: "var(--mf-muted)" }}>
          By
          <input type="date" className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <label className={label} style={{ color: "var(--mf-muted)" }}>
          Inflation (%/yr)
          <input inputMode="decimal" className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={infl} onChange={(e) => setInfl(e.target.value)} />
        </label>
      </div>
      <fieldset className="mt-2 flex flex-wrap gap-3 text-sm">
        <legend className="mb-1 text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>Funded by</legend>
        {portfolios.filter((p) => !p.archived).map((p) => (
          <label key={p.id} className="flex items-center gap-1">
            <input type="checkbox" checked={linked.includes(p.id)} onChange={(e) => setLinked((l) => (e.target.checked ? [...l, p.id] : l.filter((x) => x !== p.id)))} />
            {p.name}
          </label>
        ))}
      </fieldset>
      <div className="mt-3 flex items-center gap-2">
        <button type="submit" disabled={!ok || save.isPending} className="rounded-lg px-3 py-1.5 text-xs font-semibold disabled:opacity-50" style={{ background: "var(--mf-accent)", color: "#fff" }}>
          {goal ? "Save goal" : "Create goal"}
        </button>
        <button type="button" className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }} onClick={onDone}>Cancel</button>
        {goal && (
          <button type="button" className="ml-auto text-xs font-semibold" style={{ color: "var(--mf-muted)" }} onClick={() => save.mutate(true)}>
            Archive goal
          </button>
        )}
        {err && <span className="text-xs" style={{ color: "var(--mf-danger)" }}>{err}</span>}
      </div>
    </form>
  );
}

function GoalCard({ goal, portfolios, onEdit }: { goal: Goal; portfolios: Portfolio[]; onEdit: () => void }) {
  const [whatIf, setWhatIf] = useState("");
  const sip = parseNumber(whatIf);
  const debounced = useDebouncedValue(sip, 400);
  const { data: s, isLoading } = useQuery({
    queryKey: ["holdings", "goal", goal.id, debounced],
    queryFn: () => getGoalStatus(goal.id, debounced),
    placeholderData: (prev) => prev,
  });
  const fig = useMemo(() => {
    const p = s?.projection;
    if (!p) return null;
    const x = p.month.map((m) => +(m / 12).toFixed(2));
    return {
      data: [
        { type: "scatter", mode: "lines", x, y: p.p10, line: { width: 0 }, hoverinfo: "skip", showlegend: false },
        { type: "scatter", mode: "lines", x, y: p.p90, fill: "tonexty", fillcolor: "rgba(42,120,214,0.15)", line: { width: 0 }, name: "10th–90th percentile", hoverinfo: "skip" },
        { type: "scatter", mode: "lines", name: "Median", x, y: p.p50, line: { color: SERIES_1, width: 2 }, hovertemplate: "Year %{x:.1f}<br>Median ₹%{y:,.0f}<extra></extra>" },
        { type: "scatter", mode: "lines", name: "Money put in", x, y: p.contributed, line: { color: REFERENCE, width: 2, dash: "dot" }, hovertemplate: "Put in ₹%{y:,.0f}<extra></extra>" },
        { type: "scatter", mode: "lines", name: "Target (inflated)", x: [0, x[x.length - 1]], y: [s!.target_future, s!.target_future], line: { color: "#b91c1c", width: 1.5, dash: "dash" }, hoverinfo: "skip" },
      ],
      layout: { height: 280, legend: { orientation: "h", y: 1.18 }, margin: { t: 40, l: 80 }, hovermode: "x", xaxis: { ...AXIS, title: { text: "Years from today" } }, yaxis: { ...AXIS, tickprefix: "₹", tickformat: ",.0f", rangemode: "tozero" } },
    };
  }, [s]);

  const linkedNames = goal.portfolio_ids.map((id) => portfolios.find((p) => p.id === id)?.name ?? `#${id}`).join(", ");
  const prob = s?.probability_pct;

  return (
    <article className="rounded-lg border p-4" style={{ borderColor: "var(--mf-border)" }}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-base font-bold">{goal.name}</div>
          <div className="text-xs" style={{ color: "var(--mf-muted)" }}>
            {formatInr(Number(goal.target_amount))} in today&apos;s money by {formatDate(goal.target_date)} · {linkedNames || "no portfolios linked"}
          </div>
        </div>
        <button type="button" className="text-xs font-semibold" style={{ color: "var(--mf-accent)" }} onClick={onEdit}>Edit</button>
      </div>

      {isLoading && !s && <div className="mt-2 text-sm" style={{ color: "var(--mf-muted)" }}>Projecting…</div>}
      {s?.state === "no_portfolios" && <div className="mt-2"><Banner level="info">Link at least one portfolio to project this goal.</Banner></div>}
      {s?.state === "insufficient_history" && <div className="mt-2"><Banner level="info">The linked funds need 60+ trading days of history to project.</Banner></div>}
      {(s?.state === "reached" || s?.state === "past_due") && (
        <div className="mt-2">
          <Banner level={s.state === "reached" ? "info" : "warning"}>
            {s.state === "reached" ? "✓ Target date passed with the goal met" : "Target date passed before the goal was met"}: {formatInr(s.current_value ?? 0)} vs {formatInr(s.target_today ?? 0)}.
          </Banner>
        </div>
      )}

      {s?.state === "projected" && (
        <>
          <div className="mt-3" aria-label="Progress toward goal">
            <div className="flex justify-between text-xs" style={{ color: "var(--mf-muted)" }}>
              <span>Today {formatInr(s.current_value ?? 0)}</span>
              <span>Needed then {formatInr(s.target_future ?? 0)} ({s.inflation_pct?.toFixed(1)}% inflation over {s.years_left.toFixed(1)} yrs)</span>
            </div>
            <div className="mt-1 h-2 w-full overflow-hidden rounded" style={{ background: "var(--mf-border)" }}>
              <div className="h-2" style={{ width: `${Math.min(100, s.progress_pct ?? 0)}%`, background: SERIES_1 }} />
            </div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-5">
            <StatCard
              title="Chance of reaching it"
              value={prob !== undefined ? `${prob.toFixed(0)}%` : "-"}
              tone={prob === undefined ? "" : prob >= 75 ? "pos" : prob >= 50 ? "warn" : "neg"}
              sub={`With ${formatInr(s.sip_used ?? 0)}/month${sip !== null ? " (what-if)" : ""}`}
            />
            <StatCard title="Median outcome" value={formatInr(s.median_terminal ?? 0)} />
            {s.required_sip && (
              <>
                <StatCard title="SIP for 50% odds" value={formatInr(s.required_sip.p50)} sub="per month" />
                <StatCard title="SIP for 75% odds" value={formatInr(s.required_sip.p75)} sub="per month" />
                <StatCard
                  title="SIP for 90% odds"
                  value={formatInr(s.required_sip.p90)}
                  sub="per month"
                  tooltip={<FormulaTooltip label="Required SIP" description="On each simulated market path the final value is linear in the monthly SIP, so the SIP that works on 90% of paths is read directly from those paths, not estimated. Includes your step-up rate." />}
                />
              </>
            )}
          </div>
          <label className="mt-3 flex items-center gap-2 text-sm font-semibold">
            What if I invested
            <input inputMode="decimal" className="w-32 rounded-lg border px-2 py-1 text-sm" style={inputStyle} value={whatIf} placeholder={String(Math.round(s.current_sip ?? 0))} onChange={(e) => setWhatIf(e.target.value)} />
            a month?
          </label>
          {fig && <PlotlyChart figure={fig} />}
          <p className="text-[0.7rem]" style={{ color: "var(--mf-muted)" }}>
            A projection from the funds&apos; past behaviour, not a forecast or a guarantee. Taxes, exit loads and changes in the fund mix are not modelled.
          </p>
        </>
      )}
    </article>
  );
}
