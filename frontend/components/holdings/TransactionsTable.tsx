"use client";

import { useState } from "react";

import type { Transaction } from "@/lib/api/holdings";
import { formatDate, formatInr } from "@/lib/format";
import { TXN_TYPE_LABELS, formatUnits, type FundTxnGroup } from "@/lib/holdings";

interface Props {
  groups: FundTxnGroup[];
  /** Portfolio name per id; the Portfolio column shows only in the household view. */
  portfolioName?: (id: number) => string;
  onOpenFund: (code: number) => void;
  onEdit: (t: Transaction) => void;
  onDelete: (t: Transaction) => void;
  deleting: boolean;
}

const muted = { color: "var(--mf-muted)" };
const num = "px-3 py-2 text-right tabular-nums whitespace-nowrap";

/** The ledger as ONE table: column names once, then a full-width row per fund
 *  followed by that fund's transactions (newest first). Funds collapse. */
export function TransactionsTable({ groups, portfolioName, onOpenFund, onEdit, onDelete, deleting }: Props) {
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const toggle = (code: number) =>
    setCollapsed((s) => {
      const next = new Set(s);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  const allCollapsed = groups.length > 0 && groups.every((g) => collapsed.has(g.scheme_code));

  const headers: { label: string; right?: boolean }[] = [
    { label: "Date" },
    { label: "Type" },
    ...(portfolioName ? [{ label: "Portfolio" }] : []),
    { label: "Amount", right: true },
    { label: "Units", right: true },
    { label: "NAV", right: true },
    { label: "Stamp duty", right: true },
    { label: "Notes" },
    { label: "" },
  ];

  return (
    <div className="flex flex-col gap-2">
      {groups.length > 1 && (
        <button
          type="button"
          className="self-end text-xs font-semibold"
          style={{ color: "var(--mf-accent)" }}
          onClick={() => setCollapsed(allCollapsed ? new Set() : new Set(groups.map((g) => g.scheme_code)))}
        >
          {allCollapsed ? "Expand all" : "Collapse all"}
        </button>
      )}
      <div className="overflow-x-auto rounded-lg border" style={{ borderColor: "var(--mf-border)" }}>
        <table className="w-full text-sm">
          <thead>
            <tr style={{ borderBottom: "1px solid var(--mf-border)" }}>
              {headers.map((h, i) => (
                <th key={i} scope="col" className={`px-3 py-2.5 text-xs font-bold uppercase tracking-wider ${h.right ? "text-right" : "text-left"}`}>
                  {h.label}
                </th>
              ))}
            </tr>
          </thead>
          {groups.map((g) => {
            const open = !collapsed.has(g.scheme_code);
            return (
              <tbody key={g.scheme_code}>
                <tr style={{ background: "var(--mf-card-bg)", borderBottom: "1px solid var(--mf-border)" }}>
                  <th scope="rowgroup" colSpan={headers.length} className="px-3 py-2 text-left font-normal">
                    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          aria-expanded={open}
                          aria-label={`${open ? "Collapse" : "Expand"} ${g.name}`}
                          onClick={() => toggle(g.scheme_code)}
                          className="w-4 text-xs"
                          style={muted}
                        >
                          {open ? "▾" : "▸"}
                        </button>
                        <button type="button" className="text-left font-bold hover:underline" style={{ color: "var(--mf-accent)" }} onClick={() => onOpenFund(g.scheme_code)}>
                          {g.name}
                        </button>
                      </div>
                      <span className="text-xs tabular-nums" style={muted}>
                        {g.transactions.length} transaction{g.transactions.length === 1 ? "" : "s"} · In {formatInr(g.paid_in)}
                        {g.taken_out > 0 && ` · Out ${formatInr(g.taken_out)}`}
                      </span>
                    </div>
                  </th>
                </tr>
                {open &&
                  g.transactions.map((t) => (
                    <tr key={t.id} style={{ borderBottom: "1px solid var(--mf-border)" }}>
                      <td className="whitespace-nowrap py-2 pl-9 pr-3">{formatDate(t.trade_date)}</td>
                      <td className="whitespace-nowrap px-3 py-2">{TXN_TYPE_LABELS[t.txn_type] ?? t.txn_type}</td>
                      {portfolioName && <td className="whitespace-nowrap px-3 py-2">{portfolioName(t.portfolio_id)}</td>}
                      <td className={num}>{formatInr(Number(t.amount))}</td>
                      <td className={num}>{formatUnits(t.units)}</td>
                      <td className={num} title={t.nav_source === "user" ? "NAV from your statement" : "AMFI NAV"}>
                        {Number(t.nav).toFixed(4)}
                        {t.nav_source === "user" ? " ✎" : ""}
                      </td>
                      <td className={num}>{Number(t.stamp_duty) > 0 ? formatInr(Number(t.stamp_duty)) : "-"}</td>
                      <td className="px-3 py-2 text-xs" style={muted}>{t.notes ?? ""}</td>
                      <td className="whitespace-nowrap px-3 py-2">
                        <div className="flex justify-end gap-3 text-xs font-semibold">
                          {!t.switch_group && (
                            <button type="button" style={{ color: "var(--mf-accent)" }} onClick={() => onEdit(t)}>
                              Edit
                            </button>
                          )}
                          <button type="button" style={{ color: "var(--mf-danger)" }} disabled={deleting} onClick={() => onDelete(t)}>
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
              </tbody>
            );
          })}
          {groups.length === 0 && (
            <tbody>
              <tr>
                <td colSpan={headers.length} className="px-3 py-6 text-center" style={muted}>
                  No transactions.
                </td>
              </tr>
            </tbody>
          )}
        </table>
      </div>
    </div>
  );
}
