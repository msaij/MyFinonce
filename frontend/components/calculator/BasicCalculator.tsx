"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { applyKey, evaluate, formatNumber } from "@/lib/calculators/expression";
import { gst, percentChange, percentOf, percentShare } from "@/lib/calculators/percent";
import { NumInput, panel, pillStyle } from "./fields";

type Kind = "digit" | "op" | "fn" | "eq";

// Five columns: functions down the left, operators down the right, delete beside =.
const KEYS: { label: string; kind: Kind; aria?: string }[] = [
  { label: "AC", kind: "fn", aria: "Clear all" },
  { label: "(", kind: "fn" },
  { label: ")", kind: "fn" },
  { label: "%", kind: "fn", aria: "Percent" },
  { label: "÷", kind: "op", aria: "Divide" },
  { label: "√", kind: "fn", aria: "Square root" },
  { label: "7", kind: "digit" },
  { label: "8", kind: "digit" },
  { label: "9", kind: "digit" },
  { label: "×", kind: "op", aria: "Multiply" },
  { label: "x²", kind: "fn", aria: "Square" },
  { label: "4", kind: "digit" },
  { label: "5", kind: "digit" },
  { label: "6", kind: "digit" },
  { label: "−", kind: "op", aria: "Subtract" },
  { label: "^", kind: "fn", aria: "Power" },
  { label: "1", kind: "digit" },
  { label: "2", kind: "digit" },
  { label: "3", kind: "digit" },
  { label: "+", kind: "op", aria: "Add" },
  { label: "±", kind: "fn", aria: "Change sign" },
  { label: "0", kind: "digit" },
  { label: ".", kind: "digit", aria: "Decimal point" },
  { label: "⌫", kind: "fn", aria: "Delete last character" },
  { label: "=", kind: "eq", aria: "Equals" },
];

const KEYBOARD: Record<string, string> = { "*": "×", x: "×", X: "×", "/": "÷", "-": "−", "+": "+", "%": "%", "(": "(", ")": ")", ".": ".", ",": ".", "^": "^" };

interface HistoryItem {
  expr: string;
  result: number;
}

/** A number as the calculator writes it: minus as "−", no grouping, bracketed when negative. */
const asOperand = (v: number) => (v < 0 ? `(−${String(-v)})` : String(v));

/** A standard calculator: order of operations, powers and roots, brackets, phone-style percent,
 *  memory and a history. Nothing is kept once you leave the page. */
export function BasicCalculator() {
  const [expr, setExpr] = useState("");
  const [done, setDone] = useState(false); // the display holds a result just produced by "="
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [memory, setMemory] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);

  const input = useCallback(
    (key: string) => {
      setError(null);
      setExpr(applyKey(expr, key, done));
      setDone(false);
    },
    [expr, done]
  );

  const clear = useCallback(() => {
    setExpr("");
    setDone(false);
    setError(null);
  }, []);
  const backspace = useCallback(() => {
    setError(null);
    setDone(false);
    setExpr((cur) => cur.slice(0, -1));
  }, []);

  const closeBrackets = (e: string) => e + ")".repeat(Math.max(0, (e.match(/\(/g) ?? []).length - (e.match(/\)/g) ?? []).length));

  const equals = useCallback(() => {
    if (!expr) return;
    const full = closeBrackets(expr);
    const r = evaluate(full);
    if (!r.ok) {
      setError(r.error || "Invalid expression");
      return;
    }
    setHistory((h) => [{ expr: full, result: r.value }, ...h].slice(0, 30));
    setExpr(String(r.value).replace("-", "−"));
    setDone(true);
  }, [expr]);

  /** The value on screen: the result, or what the expression typed so far comes to. */
  const current = useMemo(() => {
    const r = evaluate(closeBrackets(expr));
    return r.ok ? r.value : null;
  }, [expr]);

  const insertValue = (v: number) => {
    setError(null);
    const base = done ? "" : expr;
    setExpr(base + (/[0-9)%]$/.test(base) ? "×" : "") + asOperand(v));
    setDone(false);
  };

  const press = (label: string) => {
    if (label === "=") equals();
    else if (label === "AC") clear();
    else if (label === "⌫") backspace();
    else input(label);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (/^[0-9]$/.test(e.key)) input(e.key);
      else if (KEYBOARD[e.key]) input(KEYBOARD[e.key]);
      else if (e.key === "Enter" || e.key === "=") equals();
      else if (e.key === "Backspace") backspace();
      else if (e.key === "Escape" || e.key === "Delete") clear();
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [input, equals, backspace, clear]);

  const showPreview = !done && /[+−×÷%^√]/.test(expr.replace(/^−/, ""));

  const copy = async () => {
    if (current == null) return;
    try {
      await navigator.clipboard.writeText(String(current));
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard blocked: nothing to do */
    }
  };

  const keyStyle = (kind: Kind) => ({
    borderColor: "var(--mf-border)",
    background: kind === "eq" ? "var(--mf-accent)" : kind === "op" ? "var(--mf-accent-bg)" : kind === "fn" ? "var(--mf-card-bg)" : "var(--mf-bg)",
    color: kind === "eq" ? "#fff" : kind === "op" ? "var(--mf-accent)" : "var(--mf-fg)",
  });

  const memBtn = (label: string, onClick: () => void, disabled = false, aria?: string) => (
    <button
      key={label}
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={aria ?? label}
      className="rounded-lg border py-1 text-xs font-semibold disabled:opacity-40"
      style={{ borderColor: "var(--mf-border)" }}
    >
      {label}
    </button>
  );

  return (
    <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(0,25rem)_minmax(0,1fr)]">
      {/* Calculator */}
      <div className="rounded-xl border p-4" style={panel}>
        <div className="relative rounded-lg border px-4 py-3 text-right" style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)" }} aria-live="polite">
          <div className="flex min-h-[1.5rem] items-start justify-between gap-2">
            <span className="text-[0.68rem] font-bold" style={{ color: "var(--mf-accent)" }} title={memory != null ? `Memory: ${formatNumber(memory)}` : undefined}>
              {memory != null ? "M" : ""}
            </span>
            <span className="break-all text-sm" style={{ color: "var(--mf-muted)" }}>{done ? history[0]?.expr ?? "" : expr || "0"}</span>
          </div>
          <div className="min-h-[2.5rem] break-all text-3xl font-bold tabular-nums">
            {error ? (
              <span className="text-base font-semibold" style={{ color: "var(--mf-danger)" }}>{error}</span>
            ) : done && current != null ? (
              `= ${formatNumber(current)}`
            ) : showPreview && current != null ? (
              <span style={{ color: "var(--mf-muted)" }}>{formatNumber(current)}</span>
            ) : (
              expr || "0"
            )}
          </div>
          <button
            type="button"
            onClick={copy}
            disabled={current == null}
            className="mt-1 text-[0.7rem] font-semibold disabled:opacity-40"
            style={{ color: "var(--mf-accent)" }}
          >
            {copied ? "Copied ✓" : "Copy result"}
          </button>
        </div>

        <div className="mt-3 grid grid-cols-4 gap-2">
          {memBtn("MC", () => setMemory(null), memory == null, "Memory clear")}
          {memBtn("MR", () => memory != null && insertValue(memory), memory == null, "Memory recall")}
          {memBtn("M+", () => current != null && setMemory((m) => (m ?? 0) + current), current == null, "Add to memory")}
          {memBtn("M−", () => current != null && setMemory((m) => (m ?? 0) - current), current == null, "Subtract from memory")}
        </div>

        <div className="mt-2 grid grid-cols-5 gap-2">
          {KEYS.map((k) => (
            <button
              key={k.label}
              type="button"
              aria-label={k.aria ?? k.label}
              onClick={() => press(k.label)}
              className="h-14 rounded-xl border text-xl font-semibold transition-opacity active:opacity-70"
              style={keyStyle(k.kind)}
            >
              {k.label}
            </button>
          ))}
        </div>
        <p className="mt-3 text-[0.72rem]" style={{ color: "var(--mf-muted)" }}>
          Keyboard works too: digits, + − * / ^, %, ( ), Enter for =, Backspace, Esc to clear. Powers come first, then × ÷, then + −. After + or −, % is a
          share of the number before it (200 + 10% = 220).
        </p>
      </div>

      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,20rem)]">
        <QuickPercentages onUse={insertValue} />

        {/* History */}
        <div className="rounded-xl border p-4" style={panel}>
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold">History</h3>
            {history.length > 0 && (
              <button type="button" onClick={() => setHistory([])} className="text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>
                Clear
              </button>
            )}
          </div>
          {history.length === 0 ? (
            <p className="mt-2 text-xs" style={{ color: "var(--mf-muted)" }}>
              Your calculations appear here; click one to reuse its result. Nothing is saved: history clears when you leave the page.
            </p>
          ) : (
            <ul className="mt-2 max-h-[34rem] divide-y overflow-y-auto" style={{ borderColor: "var(--mf-border)" }}>
              {history.map((h, i) => (
                <li key={`${h.expr}-${i}`}>
                  <button type="button" onClick={() => insertValue(h.result)} className="w-full py-2 text-right hover:opacity-80" title="Use this result">
                    <div className="break-all text-xs" style={{ color: "var(--mf-muted)" }}>{h.expr}</div>
                    <div className="font-semibold tabular-nums">= {formatNumber(h.result)}</div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

const GST_RATES = [5, 18, 40];

/** The percentage sums people reach for a calculator to do, each one line. */
function QuickPercentages({ onUse }: { onUse: (v: number) => void }) {
  const [pctOfA, setPctOfA] = useState(18);
  const [pctOfB, setPctOfB] = useState(2500);
  const [chgA, setChgA] = useState(80);
  const [chgB, setChgB] = useState(100);
  const [shareA, setShareA] = useState(45);
  const [shareB, setShareB] = useState(180);
  const [gstAmt, setGstAmt] = useState(1000);
  const [gstRate, setGstRate] = useState(18);
  const [gstMode, setGstMode] = useState<"add" | "remove">("add");

  const fin = (...v: number[]) => v.every(Number.isFinite);
  const result = (v: number | null, suffix = "") =>
    v == null ? (
      <span style={{ color: "var(--mf-muted)" }}>-</span>
    ) : (
      <button type="button" onClick={() => onUse(Number(v.toPrecision(12)))} className="font-bold tabular-nums underline-offset-2 hover:underline" title="Send to the calculator">
        {formatNumber(Number(v.toPrecision(12)))}
        {suffix}
      </button>
    );

  const row = (children: React.ReactNode, out: React.ReactNode) => (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b py-2.5 text-sm last:border-0" style={{ borderColor: "var(--mf-border)" }}>
      <div className="flex flex-wrap items-center gap-1.5">{children}</div>
      <div className="text-right">{out}</div>
    </div>
  );

  const g = fin(gstAmt, gstRate) ? gst(gstAmt, gstRate, gstMode) : null;

  return (
    <div className="rounded-xl border p-4" style={panel}>
      <h3 className="text-sm font-bold">Quick percentages</h3>
      <p className="text-xs" style={{ color: "var(--mf-muted)" }}>Click an answer to send it to the calculator.</p>
      <div className="mt-1">
        {row(
          <>
            <NumInput label="Percent" value={pctOfA} onChange={setPctOfA} className="w-16" grouped={false} />
            <span>% of</span>
            <NumInput label="Of amount" value={pctOfB} onChange={setPctOfB} className="w-28" />
          </>,
          result(fin(pctOfA, pctOfB) ? percentOf(pctOfA, pctOfB) : null)
        )}
        {row(
          <>
            <span>Change from</span>
            <NumInput label="From" value={chgA} onChange={setChgA} className="w-24" />
            <span>to</span>
            <NumInput label="To" value={chgB} onChange={setChgB} className="w-24" />
          </>,
          (() => {
            const v = fin(chgA, chgB) ? percentChange(chgA, chgB) : null;
            return v == null ? result(null) : <span className={v > 0 ? "mf-pos" : v < 0 ? "mf-neg" : ""}>{v > 0 ? "+" : ""}{result(v, "%")}</span>;
          })()
        )}
        {row(
          <>
            <NumInput label="Part" value={shareA} onChange={setShareA} className="w-24" />
            <span>is what % of</span>
            <NumInput label="Whole" value={shareB} onChange={setShareB} className="w-24" />
          </>,
          result(fin(shareA, shareB) ? percentShare(shareA, shareB) : null, "%")
        )}
        <div className="py-2.5 text-sm">
          <div className="flex flex-wrap items-center gap-1.5">
            <span>GST</span>
            {(["add", "remove"] as const).map((m) => (
              <button key={m} type="button" onClick={() => setGstMode(m)} className="rounded-full border px-2.5 py-0.5 text-xs font-semibold" style={pillStyle(gstMode === m)}>
                {m === "add" ? "Add to" : "Remove from"}
              </button>
            ))}
            <NumInput label="Amount" value={gstAmt} onChange={setGstAmt} className="w-28" />
            <span>at</span>
            {GST_RATES.map((r) => (
              <button key={r} type="button" onClick={() => setGstRate(r)} className="rounded-full border px-2 py-0.5 text-xs font-semibold" style={pillStyle(gstRate === r)}>
                {r}%
              </button>
            ))}
            <NumInput label="GST rate" value={gstRate} onChange={setGstRate} className="w-14" grouped={false} />
            <span>%</span>
          </div>
          {g && (
            <div className="mt-2 grid grid-cols-3 gap-2 text-center">
              {[
                ["Before GST", g.net],
                ["GST", g.tax],
                ["Total", g.gross],
              ].map(([k, v]) => (
                <div key={k as string} className="rounded-lg border px-2 py-1.5" style={{ borderColor: "var(--mf-border)" }}>
                  <div className="text-[0.68rem] font-semibold uppercase" style={{ color: "var(--mf-muted)" }}>{k}</div>
                  {result(v as number)}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
