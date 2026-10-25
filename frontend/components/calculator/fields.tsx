"use client";

import { useRef, useState } from "react";
import { calculateNewCursor, formatIndianNumberString, plainNumber } from "@/lib/calculators/expression";

/** What a number box keeps of typed text: digits and the first decimal point. Works
 *  character by character, so keeping a prefix gives a prefix of the result. */
const keep = (s: string) => {
  const digits = s.replace(/[^0-9.]/g, "");
  const dot = digits.indexOf(".");
  return dot === -1 ? digits : digits.slice(0, dot + 1) + digits.slice(dot + 1).replace(/\./g, "");
};

/**
 * A number box for money and other figures. It shows Indian digit grouping (25,00,000)
 * both while reading and while editing in real time as digits are entered.
 */
export function NumInput({
  value,
  onChange,
  label,
  grouped = true,
  className = "w-36",
  placeholder,
  disabled,
}: {
  value: number;
  onChange: (v: number) => void;
  label: string;
  grouped?: boolean;
  className?: string;
  placeholder?: string;
  disabled?: boolean;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // plainNumber, not String(): String(1e-7) is "1e-7", which the grouping would show as 17.
  const shown = editing !== null
    ? editing
    : Number.isFinite(value)
      ? (grouped ? formatIndianNumberString(plainNumber(value)) : plainNumber(value))
      : "";

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!grouped) return;
    const input = e.currentTarget;
    const start = input.selectionStart ?? 0;
    const end = input.selectionEnd ?? 0;

    // Handle backspace immediately following a comma (e.g. "10,|000" -> delete the "0" before ",")
    if (e.key === "Backspace" && start === end && start > 0 && input.value[start - 1] === ",") {
      e.preventDefault();
      const raw = input.value;
      const updated = raw.slice(0, start - 2) + raw.slice(start);
      const digitsOnly = updated.replace(/[^0-9.]/g, "");
      const formatted = formatIndianNumberString(digitsOnly);
      const newCursor = calculateNewCursor(updated, formatted, start - 2);
      setEditing(formatted);
      onChange(digitsOnly === "" ? NaN : Number(digitsOnly));
      requestAnimationFrame(() => {
        input.setSelectionRange(newCursor, newCursor);
      });
    }

    // Handle delete immediately preceding a comma (e.g. "10|,000" -> delete the "0" after ",")
    if (e.key === "Delete" && start === end && start < input.value.length && input.value[start] === ",") {
      e.preventDefault();
      const raw = input.value;
      const updated = raw.slice(0, start) + raw.slice(start + 2);
      const digitsOnly = updated.replace(/[^0-9.]/g, "");
      const formatted = formatIndianNumberString(digitsOnly);
      const newCursor = calculateNewCursor(updated, formatted, start);
      setEditing(formatted);
      onChange(digitsOnly === "" ? NaN : Number(digitsOnly));
      requestAnimationFrame(() => {
        input.setSelectionRange(newCursor, newCursor);
      });
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const input = e.target;
    const raw = input.value;
    const cursorPos = input.selectionStart ?? raw.length;

    // Clean non-digits and non-dots
    const clean = keep(raw);

    if (!clean) {
      setEditing("");
      onChange(NaN);
      return;
    }

    const formatted = grouped ? formatIndianNumberString(clean) : clean;
    // Count only what is kept before the caret, so a stripped letter or second "." typed
    // there does not push the caret along.
    const kept = keep(raw.slice(0, cursorPos)).length;
    const newCursor = grouped ? calculateNewCursor(clean, formatted, kept) : kept;

    setEditing(formatted);
    onChange(Number(clean));

    requestAnimationFrame(() => {
      input.setSelectionRange(newCursor, newCursor);
    });
  };

  return (
    <input
      ref={inputRef}
      type="text"
      inputMode="decimal"
      value={shown}
      placeholder={placeholder}
      aria-label={label}
      disabled={disabled}
      onFocus={() => {
        if (Number.isFinite(value)) {
          setEditing(grouped ? formatIndianNumberString(plainNumber(value)) : plainNumber(value));
        } else {
          setEditing("");
        }
      }}
      onBlur={() => setEditing(null)}
      onKeyDown={handleKeyDown}
      onChange={handleChange}
      className={`rounded-lg border px-2 py-1 text-right text-sm font-semibold tabular-nums disabled:opacity-40 ${className}`}
      style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-fg)" }}
    />
  );
}

export const panel = { borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" } as const;

export function pillStyle(active: boolean) {
  return {
    borderColor: active ? "var(--mf-accent)" : "var(--mf-border)",
    background: active ? "var(--mf-accent-bg)" : "transparent",
    color: active ? "var(--mf-accent)" : "var(--mf-fg)",
  };
}
