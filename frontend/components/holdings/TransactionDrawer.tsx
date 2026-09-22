"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { Banner } from "@/components/shared/Banner";
import { SearchCombobox } from "@/components/shared/SearchCombobox";
import {
  addTransaction,
  editTransaction,
  previewTransaction,
  type Portfolio,
  type Transaction,
  type TxnType,
} from "@/lib/api/holdings";
import { useDebouncedValue } from "@/lib/hooks";
import {
  FORM_TXN_TYPES,
  OUTFLOW_TYPES,
  PURCHASE_TYPES,
  TXN_TYPE_LABELS,
  buildDraft,
  emptyForm,
  formFromTransaction,
  formatUnits,
  todayIso,
  type TxnFormState,
} from "@/lib/holdings";
import { formatInr } from "@/lib/format";

const inputStyle: React.CSSProperties = {
  borderColor: "var(--mf-border)",
  background: "var(--mf-bg)",
  color: "var(--mf-fg)",
};

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="flex flex-col gap-1 text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>
      {label}
      {children}
      {hint && <span className="text-[0.7rem] font-normal">{hint}</span>}
    </label>
  );
}

export function TransactionDrawer({
  open,
  onClose,
  portfolios,
  defaultPortfolioId,
  editing,
  prefillScheme,
}: {
  open: boolean;
  onClose: () => void;
  portfolios: Portfolio[];
  defaultPortfolioId: number | null;
  editing?: Transaction | null;
  prefillScheme?: { code: number; name: string } | null;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<TxnFormState>(() => emptyForm(defaultPortfolioId, todayIso()));
  const [schemeLabel, setSchemeLabel] = useState("");
  const [switchLabel, setSwitchLabel] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setSaveError(null);
    if (editing) {
      setForm(formFromTransaction(editing));
      setSchemeLabel(String(editing.scheme_name ?? editing.scheme_code));
    } else {
      const f = emptyForm(defaultPortfolioId, todayIso());
      if (prefillScheme) f.schemeCode = prefillScheme.code;
      setForm(f);
      setSchemeLabel(prefillScheme?.name ?? "");
    }
    setSwitchLabel("");
  }, [open, editing, defaultPortfolioId, prefillScheme]);

  const set = <K extends keyof TxnFormState>(k: K, v: TxnFormState[K]) => setForm((f) => ({ ...f, [k]: v }));

  const { draft, missing } = useMemo(() => buildDraft(form), [form]);
  const debouncedDraft = useDebouncedValue(draft, 350);

  const preview = useQuery({
    queryKey: ["holdings-preview", debouncedDraft, editing?.id ?? null],
    queryFn: () => previewTransaction(debouncedDraft!, editing?.id),
    enabled: open && debouncedDraft !== null,
    staleTime: 0,
  });

  const save = useMutation({
    mutationFn: (): Promise<{ warnings: string[] }> =>
      editing ? editTransaction(editing.id, draft!) : addTransaction(draft!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["holdings"] });
      onClose();
    },
    onError: (e: Error) => setSaveError(e.message),
  });

  if (!open) return null;

  const isOutflow = OUTFLOW_TYPES.includes(form.txnType);
  const isPurchase = PURCHASE_TYPES.includes(form.txnType) || form.txnType === "SWITCH";
  const isDividend = form.txnType.startsWith("DIVIDEND");
  const previewFresh = preview.data && debouncedDraft === draft;
  const canSave = draft !== null && previewFresh && preview.data!.ok && !save.isPending;
  const activePortfolios = portfolios.filter((p) => !p.archived);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={editing ? "Edit transaction" : "Add transaction"}>
      <button type="button" aria-label="Close" className="absolute inset-0 cursor-default" style={{ background: "rgba(15,23,42,0.35)" }} onClick={onClose} />
      <div className="relative flex h-full w-full max-w-md flex-col gap-3 overflow-y-auto p-5 shadow-xl" style={{ background: "var(--mf-bg)", color: "var(--mf-fg)" }}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold">{editing ? "Edit transaction" : "Add transaction"}</h2>
          <button type="button" onClick={onClose} className="rounded px-2 py-1 text-sm" style={{ color: "var(--mf-muted)" }}>
            ✕
          </button>
        </div>

        <Field label="Portfolio">
          <select
            className="rounded-lg border px-2 py-1.5 text-sm"
            style={inputStyle}
            value={form.portfolioId ?? ""}
            onChange={(e) => set("portfolioId", e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Choose…</option>
            {activePortfolios.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Transaction type">
          <select
            className="rounded-lg border px-2 py-1.5 text-sm"
            style={inputStyle}
            value={form.txnType}
            disabled={!!editing}
            onChange={(e) => {
              const t = e.target.value as TxnType;
              setForm((f) => ({ ...f, txnType: t, mode: OUTFLOW_TYPES.includes(t) ? "units" : "amount", redeemAll: false }));
            }}
          >
            {(editing ? [form.txnType] : FORM_TXN_TYPES).map((t) => (
              <option key={t} value={t}>
                {TXN_TYPE_LABELS[t]}
              </option>
            ))}
          </select>
        </Field>

        <Field label={form.txnType === "SWITCH" ? "Switch out of" : "Fund"}>
          {form.schemeCode && schemeLabel ? (
            <div className="flex items-center justify-between gap-2 rounded-lg border px-2 py-1.5 text-sm font-normal" style={inputStyle}>
              <span className="truncate" title={schemeLabel}>{schemeLabel}</span>
              {!editing && (
                <button type="button" className="text-xs font-semibold" style={{ color: "var(--mf-accent)" }} onClick={() => { set("schemeCode", null); setSchemeLabel(""); }}>
                  Change
                </button>
              )}
            </div>
          ) : (
            <SearchCombobox
              placeholder="Search fund name or AMFI code…"
              onSelect={(s) => {
                set("schemeCode", s.scheme_code);
                setSchemeLabel(s.display_label ?? s.scheme_name);
              }}
            />
          )}
        </Field>

        {form.txnType === "SWITCH" && (
          <Field label="Switch into">
            {form.switchTo && switchLabel ? (
              <div className="flex items-center justify-between gap-2 rounded-lg border px-2 py-1.5 text-sm font-normal" style={inputStyle}>
                <span className="truncate" title={switchLabel}>{switchLabel}</span>
                <button type="button" className="text-xs font-semibold" style={{ color: "var(--mf-accent)" }} onClick={() => { set("switchTo", null); setSwitchLabel(""); }}>
                  Change
                </button>
              </div>
            ) : (
              <SearchCombobox
                placeholder="Search the target fund…"
                onSelect={(s) => {
                  set("switchTo", s.scheme_code);
                  setSwitchLabel(s.display_label ?? s.scheme_name);
                }}
              />
            )}
          </Field>
        )}

        <Field label="Trade date" hint="The date your order was allotted (the NAV date on your statement).">
          <input type="date" className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} max={todayIso()} value={form.tradeDate} onChange={(e) => set("tradeDate", e.target.value)} />
        </Field>

        {isOutflow && (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.redeemAll} onChange={(e) => set("redeemAll", e.target.checked)} />
            {form.txnType === "SWITCH" ? "Switch all units held on that date" : "Redeem all units held on that date"}
          </label>
        )}

        {!(isOutflow && form.redeemAll) && (
          <>
            {!isDividend && (
              <div className="flex gap-1 text-xs font-semibold" role="radiogroup" aria-label="Enter by">
                {(["amount", "units"] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    role="radio"
                    aria-checked={form.mode === m}
                    onClick={() => set("mode", m)}
                    className="rounded-full px-3 py-1"
                    style={{
                      background: form.mode === m ? "var(--mf-accent)" : "transparent",
                      color: form.mode === m ? "#fff" : "var(--mf-fg)",
                      border: "1px solid var(--mf-border)",
                    }}
                  >
                    {m === "amount" ? "By amount (₹)" : "By units"}
                  </button>
                ))}
              </div>
            )}
            <Field label={isDividend ? "Dividend amount (₹)" : form.mode === "amount" ? (isOutflow ? "Proceeds received (₹)" : "Amount paid (₹)") : "Units"}>
              <input
                inputMode="decimal"
                className="rounded-lg border px-2 py-1.5 text-sm"
                style={inputStyle}
                value={form.value}
                placeholder={form.mode === "amount" || isDividend ? "e.g. 10000" : "e.g. 125.432"}
                onChange={(e) => set("value", e.target.value)}
              />
            </Field>
          </>
        )}

        <Field label="NAV (optional)" hint="Leave blank to use AMFI's published NAV for the trade date. Type your statement's NAV to override it.">
          <input inputMode="decimal" className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={form.nav} placeholder="Auto from AMFI" onChange={(e) => set("nav", e.target.value)} />
        </Field>

        {isPurchase && (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.applyStampDuty} onChange={(e) => set("applyStampDuty", e.target.checked)} />
            Deduct stamp duty (0.005%, purchases from 1 Jul 2020)
          </label>
        )}

        <Field label="Notes (optional)">
          <input className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={form.notes} maxLength={500} onChange={(e) => set("notes", e.target.value)} />
        </Field>

        <div className="rounded-lg border p-3 text-sm" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }} aria-live="polite">
          <div className="mb-1 text-xs font-bold uppercase tracking-wide" style={{ color: "var(--mf-muted)" }}>
            Preview
          </div>
          {missing ? (
            <div style={{ color: "var(--mf-muted)" }}>{missing}</div>
          ) : preview.isFetching || !previewFresh ? (
            <div style={{ color: "var(--mf-muted)" }}>Checking against your ledger…</div>
          ) : preview.isError ? (
            <div className="mf-neg">{(preview.error as Error).message}</div>
          ) : preview.data && !preview.data.ok ? (
            <div className="font-semibold" style={{ color: "var(--mf-danger)" }}>✕ {preview.data.error}</div>
          ) : preview.data ? (
            <div className="flex flex-col gap-1.5">
              {preview.data.rows.map((r, i) => (
                <div key={i} className="grid grid-cols-2 gap-x-3 gap-y-0.5">
                  <span style={{ color: "var(--mf-muted)" }}>{TXN_TYPE_LABELS[r.txn_type]}</span>
                  <span className="text-right font-semibold">{formatInr(Number(r.amount))}</span>
                  <span style={{ color: "var(--mf-muted)" }}>Units</span>
                  <span className="text-right">{formatUnits(r.units)}</span>
                  <span style={{ color: "var(--mf-muted)" }}>NAV ({r.nav_source === "user" ? "yours" : "AMFI"})</span>
                  <span className="text-right">{Number(r.nav).toFixed(4)}</span>
                  {Number(r.stamp_duty) > 0 && (
                    <>
                      <span style={{ color: "var(--mf-muted)" }}>Stamp duty</span>
                      <span className="text-right">{formatInr(Number(r.stamp_duty))}</span>
                    </>
                  )}
                </div>
              ))}
              {preview.data.warnings.map((w, i) => (
                <div key={i} className="text-xs" style={{ color: "var(--mf-warning)" }}>
                  ⚠ {w}
                </div>
              ))}
            </div>
          ) : null}
        </div>

        {saveError && <Banner level="danger">{saveError}</Banner>}

        <div className="mt-auto flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="rounded-lg border px-3 py-2 text-sm font-semibold" style={{ borderColor: "var(--mf-border)" }}>
            Cancel
          </button>
          <button
            type="button"
            disabled={!canSave}
            onClick={() => save.mutate()}
            className="rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-50"
            style={{ background: "var(--mf-accent)", color: "#fff" }}
          >
            {save.isPending ? "Saving…" : editing ? "Save changes" : "Add to ledger"}
          </button>
        </div>
      </div>
    </div>
  );
}
