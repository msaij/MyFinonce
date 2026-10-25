"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { applyKey, applyKeyAt, asOperand, calculateNewCursor, evaluate, filterTyped, formatExpression, formatNumber, plainNumber, readPaste } from "@/lib/calculators/expression";
import { percentChange, percentOf, percentShare } from "@/lib/calculators/percent";
import { GlobalNewsFeed } from "@/components/news/GlobalNewsFeed";
import { NumInput, panel } from "./fields";

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

const KEYBOARD: Record<string, string> = { "*": "×", x: "×", X: "×", "/": "÷", "-": "−", "+": "+", "%": "%", "(": "(", ")": ")", ".": ".", "^": "^" };

interface HistoryItem {
  expr: string;
  result: number;
}

/** A key or paste meant for something else on the page: already handled, typed while a
 *  dialog is open (the news preview), or aimed at the news feed beside the calculator,
 *  where Enter opens a story and Escape closes it rather than pressing "=" or "AC". */
function belongsElsewhere(e: Event): boolean {
  if (e.defaultPrevented) return true;
  if (document.querySelector('[aria-modal="true"]')) return true;
  return e.target instanceof Element && e.target.closest("[data-news-feed]") !== null;
}

/** A standard calculator: order of operations, powers and roots, brackets, phone-style percent,
 *  memory and a history. Nothing is kept once you leave the page. */
export function BasicCalculator() {
  const [expr, setExpr] = useState("");
  const [done, setDone] = useState(false); // the display holds a result just produced by "="
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [memory, setMemory] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);
  const [pasted, setPasted] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const selectionRef = useRef<{ start: number; end: number }>({ start: 0, end: 0 });

  const updateSelection = useCallback(() => {
    if (inputRef.current) {
      selectionRef.current = {
        start: inputRef.current.selectionStart ?? 0,
        end: inputRef.current.selectionEnd ?? 0,
      };
    }
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
    setExpr(full);
    setDone(true);
    selectionRef.current = { start: full.length, end: full.length };
  }, [expr]);

  /** The value on screen: the result, or what the expression typed so far comes to. */
  const current = useMemo(() => {
    if (!expr) return null;
    const r = evaluate(closeBrackets(expr));
    return r.ok ? r.value : null;
  }, [expr]);

  const clear = useCallback(() => {
    setExpr("");
    setDone(false);
    setError(null);
    selectionRef.current = { start: 0, end: 0 };
    inputRef.current?.focus();
  }, []);

  const backspace = useCallback(() => {
    setError(null);
    if (done) {
      setDone(false);
      const next = expr.slice(0, -1);
      const formatted = formatExpression(next);
      setExpr(formatted);
      selectionRef.current = { start: formatted.length, end: formatted.length };
      return;
    }
    const inputEl = inputRef.current;
    const start = selectionRef.current.start;
    const end = selectionRef.current.end;

    if (start !== end) {
      const before = expr.slice(0, start);
      const after = expr.slice(end);
      const updated = before + after;
      const formatted = formatExpression(updated);
      const newCursor = calculateNewCursor(updated, formatted, start);
      setExpr(formatted);
      selectionRef.current = { start: newCursor, end: newCursor };
      requestAnimationFrame(() => inputEl?.setSelectionRange(newCursor, newCursor));
      return;
    }

    if (start > 0) {
      const delCount = expr[start - 1] === "," ? 2 : 1;
      const targetPos = Math.max(0, start - delCount);
      const before = expr.slice(0, targetPos);
      const after = expr.slice(start);
      const updated = before + after;
      const formatted = formatExpression(updated);
      const newCursor = calculateNewCursor(updated, formatted, targetPos);
      setExpr(formatted);
      selectionRef.current = { start: newCursor, end: newCursor };
      requestAnimationFrame(() => inputEl?.setSelectionRange(newCursor, newCursor));
      return;
    }
    // Caret at the start with nothing selected: nothing to delete, as in a normal text field.
  }, [done, expr]);

  const input = useCallback(
    (key: string) => {
      setError(null);
      const inputEl = inputRef.current;
      const start = selectionRef.current.start;
      const end = selectionRef.current.end;

      if (done) {
        setDone(false);
        const continues = /[+−×÷^%±]/.test(key) || key === "x²";
        if (continues && current != null) {
          const base = asOperand(current);
          const next = applyKey(base, key, false);
          const formatted = formatExpression(next);
          setExpr(formatted);
          selectionRef.current = { start: formatted.length, end: formatted.length };
        } else {
          const next = applyKey("", key, false);
          const formatted = formatExpression(next);
          setExpr(formatted);
          selectionRef.current = { start: formatted.length, end: formatted.length };
        }
        return;
      }

      // If user has a cursor placed in the middle or text selected in the input
      if (inputEl && (start < expr.length || start !== end)) {
        // The same rules as at the end, applied to the text before the caret.
        const at = applyKeyAt(expr, start, end, key);
        if (!at) return;
        setExpr(at.text);
        selectionRef.current = { start: at.caret, end: at.caret };
        requestAnimationFrame(() => {
          inputEl.setSelectionRange(at.caret, at.caret);
        });
        return;
      }

      const next = applyKey(expr, key, false);
      const formatted = formatExpression(next);
      setExpr(formatted);
      selectionRef.current = { start: formatted.length, end: formatted.length };
    },
    [expr, done, current]
  );

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const inputEl = e.target;
    const raw = inputEl.value;
    const cursorPos = inputEl.selectionStart ?? raw.length;

    const normalized = filterTyped(raw);
    const formatted = formatExpression(normalized);
    // Filter the text before the caret too, so stripped characters do not shift the caret.
    const newCursor = calculateNewCursor(normalized, formatted, filterTyped(raw.slice(0, cursorPos)).length);

    setError(null);
    setDone(false);
    setExpr(formatted);
    selectionRef.current = { start: newCursor, end: newCursor };

    requestAnimationFrame(() => {
      inputEl.setSelectionRange(newCursor, newCursor);
    });
  };

  const handleInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    const inputEl = e.currentTarget;
    const start = inputEl.selectionStart ?? 0;
    const end = inputEl.selectionEnd ?? 0;

    if (e.key === "Enter" || e.key === "=") {
      e.preventDefault();
      equals();
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      clear();
      return;
    }
    // Straight after "=", with the caret still at the end: a key means what it means on the
    // keypad (a digit starts afresh, an operator carries on from the result), not "append".
    if (done && start === end && end === inputEl.value.length && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const k = /^[0-9]$/.test(e.key) ? e.key : KEYBOARD[e.key];
      if (k) {
        e.preventDefault();
        input(k);
        return;
      }
    }

    if (e.key === "Backspace" && start === end && start > 0) {
      if (inputEl.value[start - 1] === ",") {
        e.preventDefault();
        const val = inputEl.value;
        const before = val.slice(0, start - 2);
        const after = val.slice(start);
        const updated = before + after;
        const formatted = formatExpression(updated);
        const newCursor = calculateNewCursor(updated, formatted, start - 2);
        setError(null);
        setExpr(formatted);
        setDone(false);
        selectionRef.current = { start: newCursor, end: newCursor };
        requestAnimationFrame(() => {
          inputEl.setSelectionRange(newCursor, newCursor);
        });
        return;
      }
    }

    if (e.key === "Delete" && start === end && start < inputEl.value.length) {
      if (inputEl.value[start] === ",") {
        e.preventDefault();
        const val = inputEl.value;
        const before = val.slice(0, start);
        const after = val.slice(start + 2);
        const updated = before + after;
        const formatted = formatExpression(updated);
        const newCursor = calculateNewCursor(updated, formatted, start);
        setError(null);
        setExpr(formatted);
        setDone(false);
        selectionRef.current = { start: newCursor, end: newCursor };
        requestAnimationFrame(() => {
          inputEl.setSelectionRange(newCursor, newCursor);
        });
        return;
      }
    }
  };

  const handlePaste = useCallback(
    (raw: string) => {
      // Text that would need guessing at ("₹2.5 Cr", "50k", two numbers) is refused, not mangled.
      const read = readPaste(raw);
      if (!read.ok) {
        setError(read.error);
        return;
      }
      const sanitized = read.text;
      setError(null);
      setPasted(true);
      setTimeout(() => setPasted(false), 1200);

      const start = selectionRef.current.start;
      const end = selectionRef.current.end;

      if (done) {
        setDone(false);
        const startsWithOp = /^[+−×÷^]/.test(sanitized);
        if (startsWithOp && current != null) {
          const next = formatExpression(asOperand(current) + sanitized);
          setExpr(next);
          selectionRef.current = { start: next.length, end: next.length };
        } else {
          const next = formatExpression(sanitized);
          setExpr(next);
          selectionRef.current = { start: next.length, end: next.length };
        }
        return;
      }

      const before = expr.slice(0, start);
      const after = expr.slice(end);
      const updated = before + sanitized + after;
      const formatted = formatExpression(updated);
      const newCursor = calculateNewCursor(before + sanitized, formatted, (before + sanitized).length);
      setExpr(formatted);
      setDone(false);
      selectionRef.current = { start: newCursor, end: newCursor };
      requestAnimationFrame(() => {
        inputRef.current?.focus();
        inputRef.current?.setSelectionRange(newCursor, newCursor);
      });
    },
    [done, current, expr]
  );

  const paste = useCallback(async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) {
        handlePaste(text);
      } else {
        setError("Clipboard is empty");
      }
    } catch {
      setError("Clipboard access blocked. Use Ctrl+V to paste.");
    }
  }, [handlePaste]);

  const insertValue = (v: number) => {
    setError(null);
    const base = done ? "" : expr;
    const operand = asOperand(v);
    const next = base + (/[0-9)%]$/.test(base) ? "×" : "") + operand;
    const formatted = formatExpression(next);
    setExpr(formatted);
    setDone(false);
    selectionRef.current = { start: formatted.length, end: formatted.length };
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(formatted.length, formatted.length);
    });
  };

  const press = (label: string) => {
    if (label === "=") equals();
    else if (label === "AC") clear();
    else if (label === "⌫") backspace();
    else input(label);
  };

  const copy = useCallback(async () => {
    if (current == null) return;
    try {
      // Plain digits with an ASCII minus: String() would give 1e-7, which pastes back as 1−7.
      await navigator.clipboard.writeText(plainNumber(current));
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // Clipboard blocked or missing: Ctrl+C has already been taken, so say so.
      setError("Couldn't copy");
    }
  }, [current]);

  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      if (belongsElsewhere(e)) return;
      const el = e.target as HTMLElement;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
      const text = e.clipboardData?.getData("text");
      if (text) {
        e.preventDefault();
        handlePaste(text);
      }
    };

    const onKey = (e: KeyboardEvent) => {
      if (belongsElsewhere(e)) return;
      const el = e.target as HTMLElement;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
      // A focused button or link keeps its own Enter (a click), and outside the calculator its
      // Escape and Delete too; digits and operators typed there still reach the calculator.
      const control = e.target instanceof Element && e.target !== document.body ? e.target.closest("a, button") : null;
      if (control && (e.key === "Enter" || ((e.key === "Escape" || e.key === "Delete") && !panelRef.current?.contains(control)))) return;

      if ((e.ctrlKey || e.metaKey) && (e.key === "c" || e.key === "C")) {
        if (current != null && !window.getSelection()?.toString()) {
          e.preventDefault();
          copy();
        }
        return;
      }
      if ((e.ctrlKey || e.metaKey) && (e.key === "v" || e.key === "V")) {
        return; // Allow native paste event to fire and be captured by onPaste
      }

      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (/^[0-9]$/.test(e.key)) input(e.key);
      else if (KEYBOARD[e.key]) input(KEYBOARD[e.key]);
      else if (e.key === "Enter" || e.key === "=") equals();
      else if (e.key === "Backspace") backspace();
      else if (e.key === "Escape" || e.key === "Delete") clear();
      else return;
      e.preventDefault();
    };

    window.addEventListener("paste", onPaste);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("paste", onPaste);
      window.removeEventListener("keydown", onKey);
    };
  }, [input, equals, backspace, clear, handlePaste, copy, current]);

  const hasOp = /[+−×÷%^√]/.test(expr.replace(/^−/, ""));
  const showTwoLines = done || hasOp;

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
      {/* Calculator. Clicking a key never takes focus: the display keeps its caret, and Enter
          stays "=" instead of clicking the last key pressed again (7, Enter would give 77).
          Tab still reaches the keys, and Enter on one tabbed to presses it. */}
      <div
        ref={panelRef}
        className="rounded-xl border p-4"
        style={panel}
        onMouseDown={(e) => {
          if (!(e.target as Element).closest("button")) return;
          e.preventDefault();
          const a = document.activeElement;
          if (a instanceof HTMLElement && a !== inputRef.current) a.blur();
        }}
      >
        <div
          className="relative rounded-lg border px-4 py-3 text-right cursor-text"
          style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)" }}
          onClick={(e) => {
            if (e.target !== inputRef.current) {
              inputRef.current?.focus();
            }
          }}
          aria-live="polite"
        >
          {/* One grid, one <input> at a fixed place in the tree: switching between the one-line
              and two-line layouts only moves it between cells, so focus and caret survive. */}
          <div
            className={`grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-2 ${
              showTwoLines ? "grid-rows-[minmax(1.75rem,auto)_minmax(2.5rem,auto)]" : "grid-rows-[minmax(1.25rem,auto)_minmax(2.5rem,auto)]"
            }`}
          >
            <span className="col-start-1 row-start-1 text-[0.68rem] font-bold" style={{ color: "var(--mf-accent)" }} title={memory != null ? `Memory: ${formatNumber(memory)}` : undefined}>
              {memory != null ? "M" : ""}
            </span>
            {!showTwoLines && error && (
              <span className="col-start-2 row-start-1 justify-self-end text-xs font-semibold" style={{ color: "var(--mf-danger)" }}>
                {error}
              </span>
            )}
            <input
              ref={inputRef}
              type="text"
              inputMode="text"
              maxLength={500}
              value={formatExpression(expr)}
              onChange={handleInputChange}
              onKeyDown={handleInputKeyDown}
              onSelect={updateSelection}
              onPaste={(e) => {
                // Native paste goes through the same cleaning as the Paste button.
                e.preventDefault();
                updateSelection();
                handlePaste(e.clipboardData.getData("text"));
              }}
              onClick={(e) => {
                e.stopPropagation();
                if (done) setDone(false);
                updateSelection();
              }}
              onFocus={() => {
                if (done) setDone(false);
                updateSelection();
              }}
              placeholder="0"
              aria-label="Calculator expression"
              className={`w-full bg-transparent text-right outline-none placeholder:text-muted ${
                showTwoLines
                  ? "col-start-2 row-start-1 text-base sm:text-lg font-semibold tracking-wide"
                  : "col-span-2 col-start-1 row-start-2 text-3xl font-bold tabular-nums tracking-tight"
              }`}
              style={{ color: done ? "var(--mf-muted)" : "var(--mf-fg)" }}
              title="Click or type to edit expression"
            />
            {showTwoLines && (
              <div className="col-span-2 col-start-1 row-start-2 break-all text-3xl font-bold tabular-nums">
                {error ? (
                  <span className="text-base font-semibold" style={{ color: "var(--mf-danger)" }}>{error}</span>
                ) : current != null ? (
                  <span style={{ color: done ? "var(--mf-fg)" : "var(--mf-muted)" }}>= {formatNumber(current)}</span>
                ) : (
                  <span style={{ color: "var(--mf-muted)" }}>...</span>
                )}
              </div>
            )}
          </div>

          <div className="mt-1 flex items-center justify-between text-[0.7rem] font-semibold">
            <span className="text-[0.68rem] font-normal" style={{ color: "var(--mf-muted)" }}>
              {done ? "Editable • press Enter for =" : showTwoLines ? "Click expression to edit in place" : "Type digits or click buttons"}
            </span>
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  paste();
                }}
                className="hover:underline"
                style={{ color: "var(--mf-accent)" }}
                title="Paste from clipboard (Ctrl+V)"
              >
                {pasted ? "Pasted ✓" : "Paste"}
              </button>
              <span style={{ color: "var(--mf-border)" }}>·</span>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  copy();
                }}
                disabled={current == null}
                className="disabled:opacity-40 hover:underline"
                style={{ color: "var(--mf-accent)" }}
                title="Copy result (Ctrl+C)"
              >
                {copied ? "Copied ✓" : "Copy result"}
              </button>
            </div>
          </div>
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
          Keyboard works too: digits, + − * / ^, %, ( ), Enter for =, Backspace, Esc to clear, Ctrl+V to paste, Ctrl+C to copy. Powers come first, then × ÷, then + −. After + or −, % is a
          share of the number before it (200 + 10% = 220).
        </p>
      </div>

      <div className="grid grid-cols-1 items-start gap-5 2xl:grid-cols-[minmax(0,22rem)_minmax(0,1fr)] xl:grid-cols-1">
        {/* Left Column: Quick Percentages + History stacked (compact width) */}
        <div className="flex flex-col gap-5 w-full max-w-md">
          <QuickPercentages onUse={insertValue} />

          {/* History */}
          <div className="rounded-xl border p-4" style={panel}>
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold">History</h3>
              {history.length > 0 && (
                <button type="button" onClick={() => setHistory([])} className="text-xs font-semibold hover:underline" style={{ color: "var(--mf-muted)" }}>
                  Clear
                </button>
              )}
            </div>
            {history.length === 0 ? (
              <p className="mt-2 text-xs" style={{ color: "var(--mf-muted)" }}>
                Your calculations appear here; click one to reuse its result. Nothing is saved: history clears when you leave the page.
              </p>
            ) : (
              <ul className="mt-2 max-h-[18rem] divide-y overflow-y-auto" style={{ borderColor: "var(--mf-border)" }}>
                {history.map((h, i) => (
                  <li key={`${h.expr}-${i}`}>
                    <button type="button" onClick={() => insertValue(h.result)} className="w-full py-2 text-right hover:opacity-80" title="Use this result">
                      <div className="break-all text-xs" style={{ color: "var(--mf-muted)" }}>{formatExpression(h.expr)}</div>
                      <div className="font-semibold tabular-nums">= {formatNumber(h.result)}</div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {/* Right Column: Global Financial News Feed */}
        <div className="w-full">
          <GlobalNewsFeed />
        </div>
      </div>
    </div>
  );
}

/** The percentage sums people reach for a calculator to do, each one line. */
function QuickPercentages({ onUse }: { onUse: (v: number) => void }) {
  const [pctOfA, setPctOfA] = useState(18);
  const [pctOfB, setPctOfB] = useState(2500);
  const [chgA, setChgA] = useState(80);
  const [chgB, setChgB] = useState(100);
  const [shareA, setShareA] = useState(45);
  const [shareB, setShareB] = useState(180);

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

  return (
    <div className="rounded-xl border p-4" style={panel}>
      <h3 className="text-sm font-bold">Quick percentages</h3>
      <p className="text-xs" style={{ color: "var(--mf-muted)" }}>Click an answer to send it to the calculator.</p>
      <div className="mt-1">
        {row(
          <>
            <NumInput label="Percent" value={pctOfA} onChange={setPctOfA} className="w-16" />
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
      </div>
    </div>
  );
}
