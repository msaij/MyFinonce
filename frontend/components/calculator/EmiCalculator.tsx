"use client";

import { Fragment, useEffect, useMemo, useState } from "react";

import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { addMonths, durationLabel, emiSchedule, groupByYear, monthsBetween, type Prepayments } from "@/lib/calculators/emi";
import { NumInput, panel, pillStyle } from "./fields";

// Validated for colour-vision deficiency and contrast on both surfaces (dataviz validator):
// principal blue, prepayment teal, interest amber. Dark mode takes a lighter blue step.
const COLORS = {
  light: { principal: "#2563EB", prepayment: "#0D9488", interest: "#D97706" },
  dark: { principal: "#3B82F6", prepayment: "#0D9488", interest: "#D97706" },
};
// The "without prepayments" line is a reference, not a series: recessive grey, dashed.
const BASELINE = "#94A3B8";

const rupees = (v: number) => `Rs ${Math.round(v).toLocaleString("en-IN")}`;

/** "25,00,000" -> "25 lakh"; "1,50,00,000" -> "1.5 crore". */
export function inWords(v: number): string {
  if (v >= 1e7) return `${+(v / 1e7).toFixed(2)} crore`;
  if (v >= 1e5) return `${+(v / 1e5).toFixed(2)} lakh`;
  if (v >= 1e3) return `${+(v / 1e3).toFixed(2)} thousand`;
  return "";
}

const thisMonth = () => new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" }).slice(0, 7);
const monthLabel = (ym: string) => new Date(`${ym}-01T00:00:00Z`).toLocaleDateString("en-IN", { month: "short", year: "numeric", timeZone: "UTC" });
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const inputStyle = { borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-fg)" };

function Field({
  label,
  hint,
  value,
  onChange,
  min,
  max,
  step,
  grouped = true,
  suffix,
}: {
  label: string;
  hint?: React.ReactNode;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  grouped?: boolean;
  suffix?: React.ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>{label}</span>
        <div className="flex items-center gap-1.5">
          <NumInput label={label} value={value} onChange={onChange} grouped={grouped} />
          {suffix}
        </div>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={Number.isFinite(value) ? Math.min(Math.max(value, min), max) : min}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-2 w-full accent-blue-600"
        aria-label={`${label} slider`}
      />
      {hint && <div className="text-[0.72rem]" style={{ color: "var(--mf-muted)" }}>{hint}</div>}
    </div>
  );
}

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "accent" | "pos" }) {
  const fg = tone === "accent" ? "var(--mf-accent)" : tone === "pos" ? "var(--mf-success)" : "var(--mf-fg)";
  const bg = tone === "accent" ? "var(--mf-accent-bg)" : tone === "pos" ? "var(--mf-success-bg)" : "var(--mf-card-bg)";
  const border = tone === "accent" ? "var(--mf-accent)" : tone === "pos" ? "var(--mf-success)" : "var(--mf-border)";
  return (
    <div className="rounded-xl border p-3.5" style={{ borderColor: border, background: bg }}>
      <div className="text-[0.68rem] font-semibold uppercase" style={{ color: "var(--mf-muted)" }}>{label}</div>
      <div className="text-xl font-bold tabular-nums" style={{ color: fg }}>{value}</div>
      {sub && <div className="text-[0.72rem]" style={{ color: "var(--mf-muted)" }}>{sub}</div>}
    </div>
  );
}

let nextId = 1;

/** Home, car or personal loan: the monthly EMI, total interest, the full repayment schedule,
 *  and what prepaying does to it. Nothing entered here is saved. */
export function EmiCalculator() {
  const [amount, setAmount] = useState(2_500_000);
  const [rate, setRate] = useState(8.5);
  const [tenure, setTenure] = useState(20);
  const [unit, setUnit] = useState<"years" | "months">("years");
  const [start, setStart] = useState(thisMonth);

  const [prepayOn, setPrepayOn] = useState(false);
  const [monthly, setMonthly] = useState(0);
  const [yearly, setYearly] = useState(0);
  const [yearlyMonth, setYearlyMonth] = useState(3);
  const [stepUp, setStepUp] = useState(0);
  const [oneTime, setOneTime] = useState<{ id: number; month: string; amount: number }[]>([]);
  const [effect, setEffect] = useState<"tenure" | "emi">("tenure");

  const [basis, setBasis] = useState<"calendar" | "financial">("calendar");
  const [view, setView] = useState<"balance" | "yearly">("balance");
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [dark, setDark] = useState(false);

  useEffect(() => {
    const el = document.documentElement;
    const sync = () => setDark(el.classList.contains("dark"));
    sync();
    const obs = new MutationObserver(sync);
    obs.observe(el, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);

  const months = unit === "years" ? Math.round(tenure * 12) : Math.round(tenure);
  const problem = !(amount > 0)
    ? "Enter a loan amount above zero."
    : !(rate >= 0) || rate > 50
      ? "Enter an interest rate between 0% and 50%."
      : !(months >= 1) || months > 600
        ? "Tenure must be between 1 month and 50 years."
        : /^\d{4}-\d{2}$/.test(start)
          ? null
          : "Pick the first EMI month.";

  const plan: Prepayments = useMemo(
    () =>
      prepayOn
        ? {
            monthly: Number.isFinite(monthly) ? monthly : 0,
            yearly: Number.isFinite(yearly) ? yearly : 0,
            yearlyMonth,
            stepUpPct: effect === "tenure" && Number.isFinite(stepUp) ? stepUp : 0,
            oneTime: oneTime.filter((p) => p.amount > 0 && /^\d{4}-\d{2}$/.test(p.month)),
            effect,
          }
        : {},
    [prepayOn, monthly, yearly, yearlyMonth, stepUp, oneTime, effect]
  );
  const hasPrepay = prepayOn && (!!plan.monthly || !!plan.yearly || !!plan.stepUpPct || (plan.oneTime?.length ?? 0) > 0);

  const base = useMemo(() => (problem ? null : emiSchedule(amount, rate, months, start)), [problem, amount, rate, months, start]);
  const withPrepay = useMemo(() => (problem || !hasPrepay ? null : emiSchedule(amount, rate, months, start, plan)), [problem, hasPrepay, amount, rate, months, start, plan]);
  const result = withPrepay ?? base;
  const years = useMemo(() => (result ? groupByYear(result.rows, basis) : []), [result, basis]);
  const c = dark ? COLORS.dark : COLORS.light;

  const figure = useMemo(() => {
    if (!result || !base) return { data: [], layout: {} };
    if (view === "balance") {
      const line = (rows: typeof result.rows, name: string, color: string, dash?: string) => ({
        type: "scatter",
        mode: "lines",
        name,
        // The full loan, the month before the first EMI; then what is left after each month.
        x: [addMonths(start, -1), ...rows.map((r) => r.month)].map((m) => `${m}-01`),
        y: [amount, ...rows.map((r) => Math.round(r.balance))],
        line: { color, width: 2, dash, shape: "linear" },
        hovertemplate: `${name}: Rs %{y:,.0f}<extra></extra>`,
      });
      return {
        data: withPrepay ? [line(base.rows, "Without prepayments", BASELINE, "dash"), line(withPrepay.rows, "With prepayments", c.principal)] : [line(base.rows, "Still owed", c.principal)],
        layout: {
          height: 330,
          hovermode: "x unified",
          showlegend: !!withPrepay,
          legend: { orientation: "h", traceorder: "normal", yanchor: "bottom", y: 1.02, xanchor: "left", x: 0 },
          xaxis: { type: "date", showgrid: false, hoverformat: "%b %Y" },
          yaxis: { showgrid: true, gridcolor: "rgba(100,116,139,0.18)", tickprefix: "Rs ", separatethousands: true, rangemode: "tozero" },
          margin: { t: 30, l: 80 },
        },
      };
    }
    const bar = (name: string, key: "principal" | "prepayment" | "interest", color: string) => ({
      type: "bar",
      name,
      x: years.map((y) => y.label),
      y: years.map((y) => Math.round(y[key])),
      marker: { color, line: { color: "rgba(0,0,0,0)", width: 0 } },
      hovertemplate: `${name}: Rs %{y:,.0f}<extra></extra>`,
    });
    const traces = [bar("Principal (EMI)", "principal", c.principal)];
    if (withPrepay) traces.push(bar("Prepayment", "prepayment", c.prepayment));
    traces.push(bar("Interest", "interest", c.interest));
    return {
      data: traces,
      layout: {
        barmode: "stack",
        bargap: 0.3,
        height: 330,
        hovermode: "x unified",
        legend: { orientation: "h", traceorder: "normal", yanchor: "bottom", y: 1.02, xanchor: "left", x: 0 },
        xaxis: { type: "category", showgrid: false, tickangle: years.length > 12 ? -45 : 0 },
        yaxis: { showgrid: true, gridcolor: "rgba(100,116,139,0.18)", tickprefix: "Rs ", separatethousands: true, rangemode: "tozero" },
        margin: { t: 30, l: 80 },
      },
    };
  }, [view, result, base, withPrepay, years, c, amount, start]);

  const downloadCsv = () => {
    if (!result) return;
    const lines = [
      ["Instalment", "Month", "EMI", "Principal", "Interest", "Prepayment", "Balance"].join(","),
      ...result.rows.map((r) => [r.n, r.month, r.emi.toFixed(2), r.principal.toFixed(2), r.interest.toFixed(2), r.prepayment.toFixed(2), r.balance.toFixed(2)].join(",")),
    ];
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    a.download = `emi-schedule-${amount}-${rate}pct-${months}m${hasPrepay ? "-with-prepayments" : ""}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const toggle = (label: string) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(label)) n.delete(label);
      else n.add(label);
      return n;
    });

  const lastMonth = result?.rows.at(-1)?.month;
  const baseLast = base?.rows.at(-1)?.month;
  const saved = withPrepay && base ? base.totalInterest - withPrepay.totalInterest : 0;
  const earlier = withPrepay && lastMonth && baseLast ? monthsBetween(lastMonth, baseLast) : 0;
  const principalShare = result ? amount / result.totalPayment : 0;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-[minmax(0,23rem)_minmax(0,1fr)]">
        {/* ---- Inputs ---- */}
        <div className="space-y-5 rounded-xl border p-4" style={panel}>
          <Field
            label="Loan amount (Rs)"
            value={amount}
            onChange={setAmount}
            min={10_000}
            max={50_000_000}
            step={10_000}
            hint={Number.isFinite(amount) && inWords(amount) ? inWords(amount) : undefined}
          />
          <Field label="Interest rate (% a year)" value={rate} onChange={setRate} min={0} max={30} step={0.05} grouped={false} hint="Annual rate on the reducing balance, as lenders quote it." />
          <Field
            label="Tenure"
            value={tenure}
            onChange={setTenure}
            min={1}
            max={unit === "years" ? 30 : 360}
            step={1}
            grouped={false}
            suffix={
              <div className="flex gap-1">
                {(["years", "months"] as const).map((u) => (
                  <button
                    key={u}
                    type="button"
                    onClick={() => {
                      if (u === unit) return;
                      setTenure(u === "months" ? Math.round(tenure * 12) : Math.max(1, Math.round((tenure / 12) * 10) / 10));
                      setUnit(u);
                    }}
                    className="rounded-full border px-2 py-0.5 text-[0.7rem] font-semibold"
                    style={pillStyle(unit === u)}
                  >
                    {u === "years" ? "Yr" : "Mo"}
                  </button>
                ))}
              </div>
            }
            hint={Number.isFinite(months) && months > 0 ? `${months} monthly instalments` : undefined}
          />
          <div className="flex items-center justify-between gap-2">
            <label className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }} htmlFor="emi-start">First EMI month</label>
            <input id="emi-start" type="month" value={start} onChange={(e) => setStart(e.target.value)} className="rounded-lg border px-2 py-1 text-sm" style={inputStyle} />
          </div>

          {/* ---- Prepayments ---- */}
          <div className="border-t pt-4" style={{ borderColor: "var(--mf-border)" }}>
            <label className="flex cursor-pointer items-center justify-between gap-2">
              <span className="flex items-center text-sm font-bold">
                Prepayments
                <FormulaTooltip
                  label="Prepayments"
                  description="Money paid on top of the EMI goes straight to principal, so every later month is charged interest on a smaller balance. RBI does not allow prepayment charges on floating-rate loans to individuals; fixed-rate loans may carry a fee, so check your loan agreement."
                />
              </span>
              <input type="checkbox" checked={prepayOn} onChange={(e) => setPrepayOn(e.target.checked)} className="h-4 w-4 accent-blue-600" />
            </label>

            {prepayOn && (
              <div className="mt-3 space-y-3 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>Extra every month</span>
                  <NumInput label="Extra every month" value={monthly} onChange={setMonthly} />
                </div>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>Extra every year, in</span>
                  <div className="flex items-center gap-1.5">
                    <select value={yearlyMonth} onChange={(e) => setYearlyMonth(Number(e.target.value))} className="rounded-lg border px-1.5 py-1 text-xs" style={inputStyle} aria-label="Month of the yearly prepayment">
                      {MONTHS.map((m, i) => (
                        <option key={m} value={i + 1} className="bg-white text-slate-900">{m}</option>
                      ))}
                    </select>
                    <NumInput label="Extra every year" value={yearly} onChange={setYearly} className="w-28" />
                  </div>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center text-xs font-semibold" style={{ color: effect === "emi" ? "var(--mf-border)" : "var(--mf-muted)" }}>
                    Raise EMI each year by (%)
                    <FormulaTooltip label="EMI step-up" description="Raise the EMI on every loan anniversary, e.g. in step with pay rises. Only with 'Reduce tenure': a lower-EMI plan would undo it." />
                  </span>
                  <NumInput label="EMI step-up % a year" value={effect === "emi" ? 0 : stepUp} onChange={setStepUp} grouped={false} className="w-20" />
                </div>

                <div>
                  <div className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>One-time prepayments</div>
                  {oneTime.map((p) => (
                    <div key={p.id} className="mt-1.5 flex items-center gap-1.5">
                      <input
                        type="month"
                        value={p.month}
                        onChange={(e) => setOneTime((l) => l.map((x) => (x.id === p.id ? { ...x, month: e.target.value } : x)))}
                        className="rounded-lg border px-1.5 py-1 text-xs"
                        style={inputStyle}
                        aria-label="Prepayment month"
                      />
                      <NumInput label="Prepayment amount" value={p.amount} onChange={(v) => setOneTime((l) => l.map((x) => (x.id === p.id ? { ...x, amount: v } : x)))} className="w-28 flex-1" />
                      <button type="button" onClick={() => setOneTime((l) => l.filter((x) => x.id !== p.id))} className="px-1 text-sm" style={{ color: "var(--mf-muted)" }} aria-label="Remove this prepayment">
                        ✕
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={() => setOneTime((l) => [...l, { id: nextId++, month: start, amount: 100_000 }])}
                    className="mt-1.5 text-xs font-semibold"
                    style={{ color: "var(--mf-accent)" }}
                  >
                    + Add a one-time prepayment
                  </button>
                </div>

                <div>
                  <div className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>After each prepayment</div>
                  <div className="mt-1.5 grid grid-cols-2 gap-1.5">
                    {(
                      [
                        ["tenure", "Reduce tenure", "Same EMI, loan ends sooner. Saves more."],
                        ["emi", "Reduce EMI", "Same end date, smaller EMI."],
                      ] as const
                    ).map(([k, t, s]) => (
                      <button key={k} type="button" onClick={() => setEffect(k)} className="rounded-lg border px-2 py-1.5 text-left" style={pillStyle(effect === k)}>
                        <div className="text-xs font-bold">{t}</div>
                        <div className="text-[0.68rem]" style={{ color: "var(--mf-muted)" }}>{s}</div>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ---- Results ---- */}
        <div className="min-w-0 space-y-4">
          {problem || !result || !base ? (
            <div className="mf-banner mf-banner-warning">{problem}</div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <Tile
                  label="Monthly EMI"
                  value={rupees(result.emi)}
                  sub={withPrepay && effect === "emi" && Math.abs(withPrepay.finalEmi - base.emi) > 1 ? `drops to ${rupees(withPrepay.finalEmi)}` : withPrepay && plan.stepUpPct ? `rises to ${rupees(withPrepay.finalEmi)}` : `for ${months} months`}
                  tone="accent"
                />
                <Tile label="Total interest" value={rupees(result.totalInterest)} sub={`${((result.totalInterest / amount) * 100).toFixed(1)}% of the loan`} />
                <Tile label="Total payable" value={rupees(result.totalPayment)} sub={`on ${rupees(amount)} borrowed`} />
                <Tile label="Loan ends" value={lastMonth ? monthLabel(lastMonth) : "-"} sub={`after ${durationLabel(result.rows.length)}`} />
              </div>

              {withPrepay && (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <Tile label="Interest saved" value={rupees(saved)} sub={`${((saved / base.totalInterest) * 100).toFixed(0)}% less than ${rupees(base.totalInterest)}`} tone="pos" />
                  <Tile
                    label={effect === "emi" ? "EMI reduced by" : "Loan ends earlier by"}
                    value={effect === "emi" ? rupees(base.emi - withPrepay.finalEmi) : earlier > 0 ? durationLabel(earlier) : "-"}
                    sub={effect === "emi" ? "a month, after the last prepayment" : baseLast ? `instead of ${monthLabel(baseLast)}` : undefined}
                    tone="pos"
                  />
                  <Tile label="Total prepaid" value={rupees(withPrepay.totalPrepaid)} sub={`Each Rs 1 prepaid saved Rs ${withPrepay.totalPrepaid ? (saved / withPrepay.totalPrepaid).toFixed(2) : "0"} of interest`} />
                </div>
              )}

              {/* Part-to-whole: two parts, so one split bar with labels says it better than a pie. */}
              <div>
                <div className="flex h-3 w-full gap-[2px] overflow-hidden rounded-full" role="img" aria-label={`Principal ${Math.round(principalShare * 100)}%, interest ${Math.round((1 - principalShare) * 100)}% of total payable`}>
                  <div style={{ width: `${principalShare * 100}%`, background: c.principal }} />
                  <div style={{ width: `${(1 - principalShare) * 100}%`, background: c.interest }} />
                </div>
                <div className="mt-1.5 flex flex-wrap justify-between gap-2 text-xs">
                  <span className="flex items-center gap-1.5">
                    <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: c.principal }} />
                    Principal {Math.round(principalShare * 100)}% of what you pay
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: c.interest }} />
                    Interest {Math.round((1 - principalShare) * 100)}%
                  </span>
                </div>
              </div>

              <div className="rounded-xl border p-3" style={panel}>
                <div className="flex flex-wrap items-center justify-between gap-2 px-1">
                  <div className="flex items-center text-sm font-bold">
                    {view === "balance" ? "Loan balance over time" : "Principal and interest paid each year"}
                    <FormulaTooltip
                      label="Chart"
                      description={
                        view === "balance"
                          ? "How much you still owe, month by month. With prepayments, the dashed line is the same loan without them: the gap is what prepaying buys you."
                          : "Each EMI is the same, but early on most of it is interest; the principal share grows as the balance falls. Prepayments are shown separately."
                      }
                    />
                  </div>
                  <div className="flex gap-1.5">
                    {(
                      [
                        ["balance", "Balance"],
                        ["yearly", "Yearly split"],
                      ] as const
                    ).map(([k, t]) => (
                      <button key={k} type="button" onClick={() => setView(k)} className="rounded-full border px-3 py-0.5 text-xs font-semibold" style={pillStyle(view === k)}>
                        {t}
                      </button>
                    ))}
                  </div>
                </div>
                <PlotlyChart figure={figure} />
              </div>
            </>
          )}
        </div>
      </div>

      {/* ---- Schedule ---- */}
      {result && !problem && (
        <div className="rounded-xl border p-4" style={panel}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-sm font-bold">Repayment schedule{withPrepay ? ", with prepayments" : ""}</h3>
            <div className="flex flex-wrap items-center gap-2">
              {(["calendar", "financial"] as const).map((b) => (
                <button key={b} type="button" onClick={() => setBasis(b)} className="rounded-full border px-3 py-1 text-xs font-semibold" style={pillStyle(basis === b)}>
                  {b === "calendar" ? "Calendar year" : "Financial year (Apr–Mar)"}
                </button>
              ))}
              <FormulaTooltip label="Financial year" description="Home-loan tax deductions (interest under section 24(b), principal under 80C) are claimed per financial year, April to March." />
              <button type="button" onClick={() => setOpen(open.size ? new Set() : new Set(years.map((y) => y.label)))} className="rounded-lg border px-3 py-1 text-xs font-semibold" style={{ borderColor: "var(--mf-border)" }}>
                {open.size ? "Collapse all" : "Expand all"}
              </button>
              <button type="button" onClick={downloadCsv} className="rounded-lg border px-3 py-1 text-xs font-semibold" style={{ borderColor: "var(--mf-border)" }}>
                Download (.csv)
              </button>
            </div>
          </div>
          <div className="mt-3 overflow-x-auto rounded-lg border" style={{ borderColor: "var(--mf-border)" }}>
            <table className="w-full text-sm tabular-nums">
              <thead>
                <tr className="text-left text-xs font-bold uppercase" style={{ borderBottom: "1px solid var(--mf-border)" }}>
                  <th className="px-3 py-2">{basis === "calendar" ? "Year" : "Financial year"}</th>
                  <th className="px-3 py-2 text-right">Principal</th>
                  <th className="px-3 py-2 text-right">Interest</th>
                  {withPrepay && <th className="px-3 py-2 text-right">Prepaid</th>}
                  <th className="px-3 py-2 text-right">Total paid</th>
                  <th className="px-3 py-2 text-right">Balance</th>
                  <th className="px-3 py-2 text-right">Loan paid off</th>
                </tr>
              </thead>
              <tbody>
                {years.map((y) => {
                  const isOpen = open.has(y.label);
                  return (
                    <Fragment key={y.label}>
                      <tr style={{ borderBottom: "1px solid var(--mf-border)" }}>
                        <td className="px-3 py-2">
                          <button type="button" onClick={() => toggle(y.label)} className="font-semibold" aria-expanded={isOpen}>
                            {isOpen ? "▾" : "▸"} {y.label}
                          </button>
                          <span className="ml-2 text-xs" style={{ color: "var(--mf-muted)" }}>{y.rows.length} EMI{y.rows.length === 1 ? "" : "s"}</span>
                        </td>
                        <td className="px-3 py-2 text-right">{rupees(y.principal)}</td>
                        <td className="px-3 py-2 text-right">{rupees(y.interest)}</td>
                        {withPrepay && <td className="px-3 py-2 text-right">{y.prepayment ? rupees(y.prepayment) : "-"}</td>}
                        <td className="px-3 py-2 text-right">{rupees(y.principal + y.interest + y.prepayment)}</td>
                        <td className="px-3 py-2 text-right">{rupees(y.balance)}</td>
                        <td className="px-3 py-2 text-right">{(((amount - y.balance) / amount) * 100).toFixed(1)}%</td>
                      </tr>
                      {isOpen &&
                        y.rows.map((r) => (
                          <tr key={r.n} className="text-xs" style={{ borderBottom: "1px solid var(--mf-border)", color: "var(--mf-muted)" }}>
                            <td className="py-1.5 pl-9 pr-3">
                              {monthLabel(r.month)} <span className="opacity-70">#{r.n} · EMI {rupees(r.emi)}</span>
                            </td>
                            <td className="px-3 py-1.5 text-right">{rupees(r.principal)}</td>
                            <td className="px-3 py-1.5 text-right">{rupees(r.interest)}</td>
                            {withPrepay && <td className="px-3 py-1.5 text-right">{r.prepayment ? rupees(r.prepayment) : "-"}</td>}
                            <td className="px-3 py-1.5 text-right">{rupees(r.emi + r.prepayment)}</td>
                            <td className="px-3 py-1.5 text-right">{rupees(r.balance)}</td>
                            <td className="px-3 py-1.5 text-right">{(((amount - r.balance) / amount) * 100).toFixed(1)}%</td>
                          </tr>
                        ))}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[0.72rem]" style={{ color: "var(--mf-muted)" }}>
            Reducing-balance method with a fixed rate; prepayments are applied after that month&apos;s EMI. Amounts are rounded to the rupee for display. Lenders
            may differ slightly on day counts, fees and when a prepayment takes effect.
          </p>
        </div>
      )}
    </div>
  );
}
