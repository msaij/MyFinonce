import { formatDate, formatInr, formatSignedPct } from "@/lib/format";

/**
 * Generic table with a per-column formatting config, replacing the pervasive
 * st.column_config number/date/currency formatting used on every table on
 * every original page (4-decimal %, INR currency, YYYY-MM-DD dates) -- see
 * the migration plan's cross-cutting note #9. One shared formatter avoids
 * precision drift between pages during the migration.
 */

export type ColumnFormat = "text" | "number" | "signed_pct" | "inr" | "date";

export interface ColumnConfig {
  key: string;
  label: string;
  format?: ColumnFormat;
  decimals?: number;
}

export interface DataTableProps {
  columns: ColumnConfig[];
  rows: Record<string, unknown>[];
  keyField?: string;
}

function formatCell(value: unknown, format: ColumnFormat = "text", decimals = 2): string {
  if (value === null || value === undefined) return "-";
  switch (format) {
    case "number":
      return typeof value === "number" ? value.toFixed(decimals) : String(value);
    case "signed_pct":
      return formatSignedPct(typeof value === "number" ? value : Number(value), decimals);
    case "inr":
      return formatInr(typeof value === "number" ? value : Number(value));
    case "date":
      return formatDate(value as string);
    default:
      return String(value);
  }
}

export function DataTable({ columns, rows, keyField = "scheme_code" }: DataTableProps) {
  return (
    <div className="overflow-x-auto rounded-lg border" style={{ borderColor: "var(--mf-border)" }}>
      <table className="w-full text-sm">
        <thead>
          <tr style={{ borderBottom: "1px solid var(--mf-border)" }}>
            {columns.map((col) => (
              <th key={col.key} className="px-3 py-2 text-left font-semibold" style={{ color: "var(--mf-muted)" }}>
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={String(row[keyField] ?? i)} style={{ borderBottom: "1px solid var(--mf-border)" }}>
              {columns.map((col) => (
                <td key={col.key} className="px-3 py-2 whitespace-nowrap">
                  {formatCell(row[col.key], col.format, col.decimals)}
                </td>
              ))}
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="px-3 py-6 text-center" style={{ color: "var(--mf-muted)" }}>
                No rows to display.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
