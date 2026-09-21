"use client";

import React, { useMemo, useState } from "react";
import { formatDate, formatInr, formatSignedPct } from "@/lib/format";

/**
 * Generic table with a per-column formatting config, replacing the pervasive
 * st.column_config number/date/currency formatting used on every table on
 * every original page (4-decimal %, INR currency, YYYY-MM-DD dates) -- see
 * the migration plan's cross-cutting note #9. One shared formatter avoids
 * precision drift between pages during the migration.
 *
 * Supports interactive 3-state column sorting:
 * 1. First click: Ascending (▲)
 * 2. Second click: Descending (▼)
 * 3. Third click: Reset to normal / original order (↕)
 * Clicking a different column resets sort and sorts that column ascending.
 */

export type ColumnFormat = "text" | "number" | "signed_pct" | "inr" | "date";

export type SortDirection = "asc" | "desc" | null;

export interface ColumnConfig {
  key: string;
  label: string;
  format?: ColumnFormat;
  decimals?: number;
  render?: (row: Record<string, unknown>, value: unknown) => React.ReactNode;
  sortable?: boolean;
  sortValue?: (row: Record<string, unknown>) => unknown;
}

export interface DataTableProps {
  columns: ColumnConfig[];
  rows: Record<string, unknown>[];
  keyField?: string;
  initialSortKey?: string | null;
  initialSortDirection?: SortDirection;
  sortKey?: string | null;
  sortDirection?: SortDirection;
  onSortChange?: (sortKey: string | null, sortDirection: SortDirection) => void;
}

function formatCell(value: unknown, format: ColumnFormat = "text", decimals = 2): string {
  if (value === null || value === undefined) return "-";
  switch (format) {
    case "number": {
      if (typeof value === "string" && value.trim() === "") return "-";
      const n = typeof value === "number" ? value : Number(value);
      return !Number.isFinite(n) ? (isMissingValue(value) ? "-" : String(value)) : n.toFixed(decimals);
    }
    case "signed_pct": {
      if (typeof value === "string" && value.trim() === "") return "-";
      const n = typeof value === "number" ? value : Number(value);
      return formatSignedPct(Number.isFinite(n) ? n : null, decimals);
    }
    case "inr": {
      if (typeof value === "string" && value.trim() === "") return "-";
      const n = typeof value === "number" ? value : Number(value);
      return formatInr(Number.isFinite(n) ? n : null);
    }
    case "date":
      return formatDate(value as string);
    default:
      return String(value);
  }
}

/**
 * Determines whether a value represents missing data (null, undefined, NaN, Infinity, invalid Date, empty string, or placeholder).
 * Missing values are consistently placed at the end of sorted tables regardless of sort direction.
 */
export function isMissingValue(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "number" && !Number.isFinite(value)) return true;
  if (value instanceof Date && Number.isNaN(value.getTime())) return true;
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return true;
    // Pure hyphens, dashes (en dash, em dash, minus sign), and spaces: e.g. "-", "--", "---", "—", "–", "−", " - "
    if (/^[-—–−\s]+$/.test(trimmed)) return true;
    const lower = trimmed.toLowerCase().replace(/\s+/g, "");
    if (
      lower === "n/a" ||
      lower === "na" ||
      lower === "n.a." ||
      lower === "nd" ||
      lower === "n/d" ||
      lower === "null" ||
      lower === "none" ||
      lower === "undefined" ||
      lower === "nan" ||
      lower === "nil" ||
      lower === "notavailable" ||
      lower === "notapplicable" ||
      lower === "nodata" ||
      lower === "inf" ||
      lower === "+inf" ||
      lower === "-inf" ||
      lower === "infinity" ||
      lower === "+infinity" ||
      lower === "-infinity"
    ) {
      return true;
    }
  }
  return false;
}

/**
 * Robustly parses unknown or formatted cell values (including currency symbols,
 * percentages, commas, spaces, accounting negatives, Unicode minus, Indian units) into numeric values.
 * Returns null if the value is missing, non-finite, or unparseable.
 */
export function parseNumericValue(val: unknown): number | null {
  if (typeof val === "number") {
    return Number.isFinite(val) ? val : null;
  }
  if (typeof val === "bigint") {
    return Number(val);
  }
  if (typeof val !== "string") {
    return null;
  }
  const trimmed = val.trim();
  if (!trimmed || isMissingValue(trimmed)) {
    return null;
  }

  // Normalize typographic minus signs (Unicode minus, figure dash, en dash, em dash)
  let s = trimmed.replace(/[\u2212\u2010\u2012\u2013\u2014]/g, "-");
  let isNegative = false;

  // Accounting parenthesis format: (123.45) or (₹123.45)
  if (s.startsWith("(") && s.endsWith(")")) {
    isNegative = true;
    s = s.slice(1, -1).trim();
  }

  // Check leading minus or plus sign
  if (s.startsWith("-")) {
    isNegative = true;
    s = s.slice(1).trim();
  } else if (s.startsWith("+")) {
    s = s.slice(1).trim();
  }

  // Strip standard Indian & global currency prefixes: Rs., Rs, INR
  s = s.replace(/^(?:rs\.?|inr)\s*/i, "");
  // Remove currency symbols (INR, USD, EUR, GBP), percent signs, commas, and whitespace
  s = s.replace(/[₹$€£%]/g, "").replace(/,/g, "").replace(/\s+/g, "");

  // Check again for leading minus sign (e.g. if original was "₹ -123" or "-₹123" or "Rs. -50")
  if (s.startsWith("-")) {
    isNegative = true;
    s = s.slice(1).trim();
  } else if (s.startsWith("+")) {
    s = s.slice(1).trim();
  }

  // Indian and metric magnitude multipliers (Cr / Crore, Lakh / Lac)
  let multiplier = 1;
  if (/cr(?:ore)?s?$/i.test(s)) {
    multiplier = 1e7;
    s = s.replace(/cr(?:ore)?s?$/i, "");
  } else if (/la[ck]hs?$/i.test(s)) {
    multiplier = 1e5;
    s = s.replace(/la[ck]hs?$/i, "");
  }

  if (!s) return null;

  // Reject hexadecimal, binary, octal literals (e.g. "0x10", "0b10") and non-decimal identifiers
  if (/^0[xXbBoO]/.test(s)) return null;

  const num = Number(s);
  if (!Number.isFinite(num)) return null;

  const res = (isNegative ? -num : num) * multiplier;
  return Number.isFinite(res) ? res : null;
}

/**
 * Robustly parses unknown cell values (Date objects, timestamps, date strings, Indian DD-MM-YYYY)
 * into numeric millisecond timestamps.
 * Returns null if the value is missing or unparseable.
 */
export function parseDateValue(val: unknown, format?: ColumnFormat): number | null {
  if (val instanceof Date) {
    const t = val.getTime();
    return Number.isNaN(t) ? null : t;
  }
  if (typeof val === "number") {
    // Only parse numbers as dates if explicitly designated as format="date" (timestamps)
    if (format === "date") {
      return Number.isFinite(val) ? val : null;
    }
    return null;
  }
  if (typeof val !== "string") {
    return null;
  }
  const trimmed = val.trim();
  if (!trimmed || isMissingValue(trimmed)) {
    return null;
  }

  // Numeric strings like "123", "1.5", "-2.4" are numbers, not dates
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) {
    if (format === "date" && /^\d{9,13}$/.test(trimmed)) {
      const num = Number(trimmed);
      return Number.isFinite(num) ? num : null;
    }
    return null;
  }

  // Indian / European DD-MM-YYYY or DD/MM/YYYY (with 4-digit year)
  const dmyMatch = trimmed.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$/);
  if (dmyMatch) {
    const p1 = parseInt(dmyMatch[1], 10);
    const p2 = parseInt(dmyMatch[2], 10);
    const yr = parseInt(dmyMatch[3], 10);
    let day = p1;
    let month = p2;
    if (p1 > 12) {
      day = p1;
      month = p2;
    } else if (p2 > 12) {
      day = p2;
      month = p1;
    }
    if (month < 1 || month > 12 || day < 1 || day > 31) {
      return null;
    }
    const d = new Date(Date.UTC(yr, month - 1, day));
    if (Number.isNaN(d.getTime())) return null;
    // Strict calendar validity check: prevent auto-rollover (e.g. 31/02/2024 -> March 2)
    if (d.getUTCFullYear() !== yr || d.getUTCMonth() !== month - 1 || d.getUTCDate() !== day) {
      return null;
    }
    return d.getTime();
  }

  // ISO format: YYYY-MM-DD or YYYY/MM/DD
  const isoMatch = trimmed.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T\s].*)?$/);
  if (isoMatch) {
    const yr = parseInt(isoMatch[1], 10);
    const month = parseInt(isoMatch[2], 10);
    const day = parseInt(isoMatch[3], 10);
    if (month < 1 || month > 12 || day < 1 || day > 31) {
      return null;
    }
    const d = new Date(Date.UTC(yr, month - 1, day));
    if (Number.isNaN(d.getTime())) return null;
    // Strict calendar validity check: prevent auto-rollover (e.g. 2024-02-31 -> March 2)
    if (d.getUTCFullYear() !== yr || d.getUTCMonth() !== month - 1 || d.getUTCDate() !== day) {
      return null;
    }
    if (/[T\s]\d/.test(trimmed)) {
      const t = Date.parse(trimmed);
      return Number.isNaN(t) ? null : t;
    }
    return d.getTime();
  }

  // Text with month name and year / day: e.g. "15 Mar 2024", "March 15, 2024", "15-Mar-2024"
  if (/\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b/i.test(trimmed)) {
    const t = Date.parse(trimmed);
    return Number.isNaN(t) ? null : t;
  }

  if (format === "date") {
    const t = Date.parse(trimmed);
    return Number.isNaN(t) ? null : t;
  }

  return null;
}

/**
 * Helper to determine if a value is missing or invalid for a specific column format.
 */
export function isMissingOrInvalid(value: unknown, format?: ColumnFormat): boolean {
  if (isMissingValue(value)) return true;
  if (format === "number" || format === "signed_pct" || format === "inr") {
    return parseNumericValue(value) === null;
  }
  if (format === "date") {
    return parseDateValue(value, format) === null;
  }
  return false;
}

/**
 * Infers the dominant ColumnFormat ("number", "date", or "text") for an unformatted column
 * by scanning non-missing row values. Prevents invalid/corrupt values from inverting in descending sort.
 */
export function inferColumnFormat(
  rows: Record<string, unknown>[],
  sortKey: string,
  sortValue?: (row: Record<string, unknown>) => unknown
): ColumnFormat {
  let numberCount = 0;
  let dateCount = 0;
  let textCount = 0;

  for (const row of rows) {
    if (!row) continue;
    let val: unknown;
    if (sortValue) {
      try {
        val = sortValue(row);
      } catch {
        continue;
      }
    } else {
      val = row[sortKey];
    }

    if (isMissingValue(val)) continue;

    if (typeof val === "number" || typeof val === "bigint") {
      numberCount++;
      continue;
    }
    if (val instanceof Date) {
      dateCount++;
      continue;
    }
    if (typeof val === "string") {
      const trimmed = val.trim();
      if (parseDateValue(trimmed) !== null) {
        dateCount++;
      } else if (parseNumericValue(trimmed) !== null) {
        numberCount++;
      } else {
        textCount++;
      }
    }
  }

  if (dateCount > numberCount && dateCount > textCount) {
    return "date";
  }
  if (numberCount > dateCount && numberCount > textCount) {
    return "number";
  }
  return "text";
}

/**
 * Type-aware comparator for table cells:
 * - Numbers and numeric metrics sort numerically.
 * - Dates sort chronologically.
 * - Text strings sort alphabetically (case-insensitive, natural numeric collation).
 */
export function compareTableValues(
  valA: unknown,
  valB: unknown,
  format?: ColumnFormat
): number {
  let effectiveFormat = format;
  if (!effectiveFormat) {
    if (valA instanceof Date || valB instanceof Date) {
      effectiveFormat = "date";
    } else {
      const isDateA = typeof valA === "string" && parseDateValue(valA) !== null;
      const isDateB = typeof valB === "string" && parseDateValue(valB) !== null;
      if (isDateA && isDateB) {
        effectiveFormat = "date";
      } else {
        const numA = parseNumericValue(valA);
        const numB = parseNumericValue(valB);
        if (numA !== null && numB !== null) {
          effectiveFormat = "number";
        } else if (numA !== null && (isMissingValue(valB) || (!isDateB && typeof valB === "string"))) {
          effectiveFormat = "number";
        } else if (numB !== null && (isMissingValue(valA) || (!isDateA && typeof valA === "string"))) {
          effectiveFormat = "number";
        } else if (isDateA || isDateB) {
          effectiveFormat = "date";
        }
      }
    }
  }

  const aMissing = isMissingOrInvalid(valA, effectiveFormat);
  const bMissing = isMissingOrInvalid(valB, effectiveFormat);

  if (aMissing && bMissing) return 0;
  if (aMissing) return 1;
  if (bMissing) return -1;

  // 1. Explicit or Inferred Date format
  if (effectiveFormat === "date") {
    const timeA = parseDateValue(valA, "date");
    const timeB = parseDateValue(valB, "date");
    if (timeA !== null && timeB !== null) {
      return timeA - timeB;
    }
  }

  // 2. Explicit or Inferred Numeric formats: number, signed_pct, inr
  if (effectiveFormat === "number" || effectiveFormat === "signed_pct" || effectiveFormat === "inr") {
    const numA = parseNumericValue(valA);
    const numB = parseNumericValue(valB);
    if (numA !== null && numB !== null) {
      return numA - numB;
    }
  }

  // 3. Explicit Text format: skip all number/date parsing
  if (effectiveFormat === "text") {
    const strA = String(valA);
    const strB = String(valB);
    const cmp = strA.localeCompare(strB, undefined, { sensitivity: "base", numeric: true });
    if (cmp !== 0) return cmp;
    return strA.localeCompare(strB);
  }

  // 4. Raw numbers or numeric strings (including decimals, currency, percentages)
  const numA = parseNumericValue(valA);
  const numB = parseNumericValue(valB);
  if (numA !== null && numB !== null) {
    return numA - numB;
  }

  // 5. Unspecified format: string dates
  if (typeof valA === "string" && typeof valB === "string") {
    const timeA = parseDateValue(valA);
    const timeB = parseDateValue(valB);
    if (timeA !== null && timeB !== null) {
      return timeA - timeB;
    }
  }

  // 6. Text strings: case-insensitive alphabetical sort with natural numeric ordering
  const strA = String(valA);
  const strB = String(valB);
  const cmp = strA.localeCompare(strB, undefined, { sensitivity: "base", numeric: true });
  if (cmp !== 0) return cmp;
  return strA.localeCompare(strB);
}

/**
 * Stable row sorter handling 3-state sorting and placing missing/null/invalid values consistently at the end.
 */
export function sortTableRows(
  rows: Record<string, unknown>[],
  sortKey: string | null,
  sortDirection: SortDirection,
  columns: ColumnConfig[]
): Record<string, unknown>[] {
  const safeRows = Array.isArray(rows) ? rows : [];
  if (!sortKey || !sortDirection || safeRows.length <= 1) {
    return safeRows;
  }

  const safeCols = Array.isArray(columns) ? columns : [];
  const col = safeCols.find((c) => c.key === sortKey);
  if (col && col.sortable === false) {
    return safeRows;
  }

  // Infer effective format if not explicitly set on the column config
  const format = col?.format ?? inferColumnFormat(safeRows, sortKey, col?.sortValue);

  const getRowVal = (row: Record<string, unknown> | undefined) => {
    if (!row) return undefined;
    if (col?.sortValue) {
      try {
        return col.sortValue(row);
      } catch {
        return undefined;
      }
    }
    return row[sortKey];
  };

  // Preserve original indices to guarantee stable sorting
  const indexed = safeRows.map((row, index) => ({ row, index }));

  indexed.sort((itemA, itemB) => {
    const rawValA = getRowVal(itemA.row);
    const rawValB = getRowVal(itemB.row);

    const aMissing = isMissingOrInvalid(rawValA, format);
    const bMissing = isMissingOrInvalid(rawValB, format);

    // If both are missing or invalid, preserve original relative ordering
    if (aMissing && bMissing) {
      return itemA.index - itemB.index;
    }
    // Null, undefined, and missing values are consistently placed at the end regardless of asc/desc
    if (aMissing) {
      return 1;
    }
    if (bMissing) {
      return -1;
    }

    const cmp = compareTableValues(rawValA, rawValB, format);
    if (cmp !== 0) {
      return sortDirection === "asc" ? cmp : -cmp;
    }
    return itemA.index - itemB.index;
  });

  return indexed.map((item) => item.row);
}

export function DataTable({
  columns = [],
  rows = [],
  keyField = "scheme_code",
  initialSortKey = null,
  initialSortDirection = null,
  sortKey: controlledSortKey,
  sortDirection: controlledSortDirection,
  onSortChange,
}: DataTableProps) {
  const isControlled = controlledSortKey !== undefined || controlledSortDirection !== undefined;
  const [internalSortKey, setInternalSortKey] = useState<string | null>(initialSortKey);
  const [internalSortDir, setInternalSortDir] = useState<SortDirection>(initialSortDirection);

  const activeSortKey = isControlled ? (controlledSortKey ?? null) : internalSortKey;
  const activeSortDir = isControlled ? (controlledSortDirection ?? null) : internalSortDir;

  const handleHeaderClick = (colKey: string) => {
    let nextKey: string | null = colKey;
    let nextDir: SortDirection = "asc";

    if (activeSortKey === colKey) {
      if (activeSortDir === "asc") {
        nextDir = "desc";
      } else if (activeSortDir === "desc") {
        nextKey = null;
        nextDir = null;
      } else {
        nextDir = "asc";
      }
    } else {
      nextKey = colKey;
      nextDir = "asc";
    }

    if (!isControlled) {
      setInternalSortKey(nextKey);
      setInternalSortDir(nextDir);
    }
    onSortChange?.(nextKey, nextDir);
  };

  const sortedRows = useMemo(
    () => sortTableRows(rows, activeSortKey, activeSortDir, columns),
    [rows, activeSortKey, activeSortDir, columns]
  );

  return (
    <div className="overflow-x-auto rounded-lg border" style={{ borderColor: "var(--mf-border)" }}>
      <table className="w-full text-sm">
        <thead>
          <tr style={{ borderBottom: "1px solid var(--mf-border)" }}>
            {columns.map((col) => {
              const isSortable = col.sortable !== false;
              const isSorted = activeSortKey === col.key;
              const ariaSort = !isSortable
                ? undefined
                : isSorted
                ? activeSortDir === "asc"
                  ? "ascending"
                  : activeSortDir === "desc"
                  ? "descending"
                  : "none"
                : "none";

              return (
                <th
                  key={col.key}
                  role={isSortable ? "columnheader" : undefined}
                  tabIndex={isSortable ? 0 : undefined}
                  aria-sort={ariaSort}
                  data-sort-key={col.key}
                  data-sort-direction={isSorted && activeSortDir ? activeSortDir : "none"}
                  onClick={isSortable ? () => handleHeaderClick(col.key) : undefined}
                  onMouseDown={
                    isSortable
                      ? (e) => {
                          if (e.detail > 1) e.preventDefault();
                        }
                      : undefined
                  }
                  onKeyDown={
                    isSortable
                      ? (e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            handleHeaderClick(col.key);
                          }
                        }
                      : undefined
                  }
                  className={`px-3 py-2.5 text-left text-xs font-bold uppercase tracking-wider transition-colors select-none ${
                    isSortable
                      ? "cursor-pointer group hover:bg-black/5 dark:hover:bg-white/5 focus-visible:outline-2 focus-visible:outline-blue-500 focus-visible:outline-offset-[-2px]"
                      : ""
                  }`}
                  style={{ color: "var(--mf-fg)", userSelect: "none" }}
                  title={
                    isSortable
                      ? isSorted
                        ? activeSortDir === "asc"
                          ? "Sorted ascending. Click to sort descending."
                          : activeSortDir === "desc"
                          ? "Sorted descending. Click to reset sort."
                          : "Click to sort ascending."
                        : `Click to sort by ${col.label}`
                      : undefined
                  }
                >
                  <div className="inline-flex items-center gap-1.5">
                    <span>{col.label}</span>
                    {isSortable && (
                      <span
                        className="inline-flex items-center text-[10px] leading-none"
                        data-testid={`sort-indicator-${col.key}`}
                        aria-hidden="true"
                      >
                        {isSorted && activeSortDir === "asc" ? (
                          <span
                            className="font-bold text-blue-600 dark:text-blue-400"
                            style={{ color: "var(--mf-accent)" }}
                            title="Sorted ascending"
                          >
                            ▲
                          </span>
                        ) : isSorted && activeSortDir === "desc" ? (
                          <span
                            className="font-bold text-blue-600 dark:text-blue-400"
                            style={{ color: "var(--mf-accent)" }}
                            title="Sorted descending"
                          >
                            ▼
                          </span>
                        ) : (
                          <span
                            className="opacity-20 group-hover:opacity-70 transition-opacity"
                            style={{ color: "var(--mf-muted)" }}
                            title="Unsorted"
                          >
                            ↕
                          </span>
                        )}
                      </span>
                    )}
                  </div>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row, i) => {
            if (!row) return null;
            return (
              <tr key={row[keyField] != null ? String(row[keyField]) : `row-${i}`} style={{ borderBottom: "1px solid var(--mf-border)" }}>
                {columns.map((col) => (
                  <td key={col.key} className="px-3 py-2 whitespace-nowrap font-medium" style={{ color: "var(--mf-fg)" }}>
                    {col.render ? col.render(row, row[col.key]) : formatCell(row[col.key], col.format, col.decimals)}
                  </td>
                ))}
              </tr>
            );
          })}
          {sortedRows.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="px-3 py-6 text-center font-medium" style={{ color: "var(--mf-muted)" }}>
                No rows to display.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
