"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Banner } from "@/components/shared/Banner";
import {
  ackAlerts,
  createAlertRule,
  deleteAlertRule,
  getInsights,
  listAlertRules,
  listAlerts,
  type AlertRule,
  type Insight,
  type Portfolio,
  type PortfolioKey,
} from "@/lib/api/holdings";
import { formatDate } from "@/lib/format";

// Severity is never colour alone: each carries an icon and a word.
const SEVERITY: Record<Insight["severity"], { icon: string; label: string; color: string }> = {
  danger: { icon: "⛔", label: "Needs attention", color: "var(--mf-danger)" },
  warning: { icon: "⚠", label: "Worth a look", color: "var(--mf-warning)" },
  info: { icon: "ℹ", label: "For your information", color: "var(--mf-accent)" },
};

const THRESHOLD_HINT: Record<AlertRule["kind"], { label: string; default: number | null } | null> = {
  drift: { label: "Min drift (pp)", default: 5 },
  drawdown: { label: "Fall below peak (%)", default: 10 },
  stale_nav: { label: "Days without NAV", default: 30 },
  regular_plan: null,
};

export function InsightsPanel({ pid, portfolios, onOpenScheme }: { pid: PortfolioKey; portfolios: Portfolio[]; onOpenScheme: (code: number) => void }) {
  const { data, isLoading, isError, error } = useQuery({ queryKey: ["holdings", "insights", pid], queryFn: () => getInsights(pid) });
  return (
    <div className="mt-4 flex flex-col gap-8">
      <section className="flex flex-col gap-3">
        <div>
          <h3 className="text-base font-bold">Insights</h3>
          <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
            Rule-based observations from your holdings and official AMFI data. Each one shows the numbers behind it. They are prompts to look closer, not recommendations.
          </p>
        </div>
        {isLoading && <div className="text-sm" style={{ color: "var(--mf-muted)" }}>Checking your portfolio…</div>}
        {isError && <Banner level="danger">{(error as Error).message}</Banner>}
        {data && data.insights.length === 0 && (
          <div className="rounded-lg border p-4 text-sm" style={{ borderColor: "var(--mf-border)" }}>
            ✓ Nothing flagged. No stale NAVs, costly Regular plans, category laggards, concentration or overlap found.
          </div>
        )}
        {data?.insights.map((i, n) => {
          const s = SEVERITY[i.severity];
          return (
            <article key={n} className="rounded-lg border p-3" style={{ borderColor: "var(--mf-border)", borderLeft: `4px solid ${s.color}`, background: "var(--mf-card-bg)" }}>
              <div className="flex items-center gap-2 text-[0.7rem] font-bold uppercase tracking-wide" style={{ color: s.color }}>
                <span aria-hidden>{s.icon}</span> {s.label}
              </div>
              <div className="mt-0.5 text-sm font-bold">{i.title}</div>
              <p className="mt-1 text-sm" style={{ color: "var(--mf-muted)" }}>{i.detail}</p>
              {i.scheme_codes.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-3 text-xs font-semibold">
                  {i.scheme_codes.map((c) => (
                    <button key={c} type="button" style={{ color: "var(--mf-accent)" }} onClick={() => onOpenScheme(c)}>
                      View holding {c} →
                    </button>
                  ))}
                </div>
              )}
            </article>
          );
        })}
      </section>
      <AlertsSection portfolios={portfolios} />
    </div>
  );
}

function AlertsSection({ portfolios }: { portfolios: Portfolio[] }) {
  const queryClient = useQueryClient();
  const rulesQ = useQuery({ queryKey: ["holdings", "alert-rules"], queryFn: listAlertRules });
  const alertsQ = useQuery({ queryKey: ["holdings", "alerts"], queryFn: () => listAlerts(false) });
  const [kind, setKind] = useState<AlertRule["kind"]>("drift");
  const [scope, setScope] = useState<string>("all");
  const [threshold, setThreshold] = useState<string>("5");
  const [err, setErr] = useState<string | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["holdings"] });

  const create = useMutation({
    mutationFn: () =>
      createAlertRule({
        kind,
        portfolio_id: scope === "all" ? null : Number(scope),
        threshold: THRESHOLD_HINT[kind] ? Number(threshold) : null,
      }),
    onSuccess: () => {
      setErr(null);
      refresh();
    },
    onError: (e: Error) => setErr(e.message),
  });
  const remove = useMutation({ mutationFn: deleteAlertRule, onSuccess: refresh });
  const ack = useMutation({ mutationFn: (ids?: number[]) => ackAlerts(ids), onSuccess: refresh });

  const kinds = rulesQ.data?.kinds ?? {};
  const pname = (id: number | null) => (id === null ? "All portfolios" : portfolios.find((p) => p.id === id)?.name ?? `#${id}`);
  const unacked = (alertsQ.data?.alerts ?? []).filter((a) => !a.ack_at);
  const inputStyle: React.CSSProperties = { borderColor: "var(--mf-border)", background: "var(--mf-bg)", color: "var(--mf-fg)" };

  return (
    <section className="flex flex-col gap-3">
      <div>
        <h3 className="text-base font-bold">Alerts</h3>
        <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
          Checked after every AMFI NAV sync, and when you add a rule. Each condition alerts once per month, not every day it stays true. Alerts appear here and as a badge in the sidebar. Nothing is emailed.
        </p>
      </div>

      {unacked.length > 0 && (
        <div className="flex flex-col gap-2">
          {unacked.map((a) => (
            <div key={a.id} className="flex items-start justify-between gap-3 rounded-lg border p-2 text-sm" style={{ borderColor: "var(--mf-border)", borderLeft: `4px solid ${SEVERITY[a.severity].color}` }}>
              <div>
                <span aria-hidden>{SEVERITY[a.severity].icon}</span> {a.message}
                <div className="text-[0.7rem]" style={{ color: "var(--mf-muted)" }}>{formatDate(a.fired_at)}</div>
              </div>
              <button type="button" className="text-xs font-semibold" style={{ color: "var(--mf-accent)" }} onClick={() => ack.mutate([a.id])}>
                Dismiss
              </button>
            </div>
          ))}
          {unacked.length > 1 && (
            <button type="button" className="self-start text-xs font-semibold" style={{ color: "var(--mf-accent)" }} onClick={() => ack.mutate(undefined)}>
              Dismiss all
            </button>
          )}
        </div>
      )}

      <div className="rounded-lg border p-3" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
        <div className="text-sm font-bold">Your rules</div>
        {(rulesQ.data?.rules ?? []).length === 0 ? (
          <div className="mt-1 text-xs" style={{ color: "var(--mf-muted)" }}>No rules yet.</div>
        ) : (
          <ul className="mt-1 flex flex-col gap-1 text-sm">
            {rulesQ.data!.rules.map((r) => (
              <li key={r.id} className="flex items-center justify-between gap-2">
                <span>
                  {kinds[r.kind] ?? r.kind}
                  {r.threshold !== null && THRESHOLD_HINT[r.kind] ? ` · ${Number(r.threshold)}` : ""} · <i>{pname(r.portfolio_id)}</i>
                </span>
                <button type="button" className="text-xs font-semibold" style={{ color: "var(--mf-danger)" }} onClick={() => remove.mutate(r.id)}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
        <form
          className="mt-3 flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>
            When
            <select className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={kind} onChange={(e) => { const k = e.target.value as AlertRule["kind"]; setKind(k); setThreshold(String(THRESHOLD_HINT[k]?.default ?? "")); }}>
              {Object.entries(kinds).map(([k, label]) => (
                <option key={k} value={k}>{label}</option>
              ))}
            </select>
          </label>
          {THRESHOLD_HINT[kind] && (
            <label className="flex flex-col gap-1 text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>
              {THRESHOLD_HINT[kind]!.label}
              <input inputMode="decimal" className="w-28 rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={threshold} onChange={(e) => setThreshold(e.target.value)} />
            </label>
          )}
          <label className="flex flex-col gap-1 text-xs font-semibold" style={{ color: "var(--mf-muted)" }}>
            For
            <select className="rounded-lg border px-2 py-1.5 text-sm" style={inputStyle} value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="all">All portfolios</option>
              {portfolios.filter((p) => !p.archived).map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </label>
          <button type="submit" className="rounded-lg px-3 py-2 text-xs font-semibold" style={{ background: "var(--mf-accent)", color: "#fff" }} disabled={create.isPending}>
            Add rule
          </button>
          {kind === "drift" && scope === "all" && <span className="text-[0.7rem]" style={{ color: "var(--mf-muted)" }}>Drift needs a portfolio with targets.</span>}
        </form>
        {err && <div className="mt-2"><Banner level="danger">{err}</Banner></div>}
      </div>
    </section>
  );
}
