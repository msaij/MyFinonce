"use client";

import { useState } from "react";

/**
 * A number box for money and other figures. It shows Indian digit grouping (25,00,000) while
 * you read it and the plain number while you edit it, so the cursor never jumps between commas.
 */
export function NumInput({
  value,
  onChange,
  label,
  grouped = true,
  className = "w-36",
  placeholder,
}: {
  value: number;
  onChange: (v: number) => void;
  label: string;
  grouped?: boolean;
  className?: string;
  placeholder?: string;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const shown = editing ?? (Number.isFinite(value) ? (grouped ? value.toLocaleString("en-IN", { maximumFractionDigits: 4 }) : String(value)) : "");
  return (
    <input
      type="text"
      inputMode="decimal"
      value={shown}
      placeholder={placeholder}
      aria-label={label}
      onFocus={() => setEditing(Number.isFinite(value) ? String(value) : "")}
      onBlur={() => setEditing(null)}
      onChange={(e) => {
        const raw = e.target.value.replace(/[^0-9.]/g, "");
        setEditing(raw);
        onChange(raw === "" ? NaN : Number(raw));
      }}
      className={`rounded-lg border px-2 py-1 text-right text-sm font-semibold tabular-nums ${className}`}
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
