"use client";

import { useMemo } from "react";

import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import type { NseIssueDetail } from "@/lib/api/nseIpo";
import { ipoSignals, type IssueContext, type Tone } from "@/lib/nseIpoSignals";

const TONE: Record<Tone, { fg: string; bg: string }> = {
  pos: { fg: "var(--mf-success)", bg: "var(--mf-success-bg)" },
  neg: { fg: "var(--mf-danger)", bg: "var(--mf-danger-bg)" },
  warn: { fg: "var(--mf-warning)", bg: "var(--mf-warning-bg)" },
  neutral: { fg: "var(--mf-fg)", bg: "var(--mf-card-bg)" },
};

/** "Should I take a chance on this IPO?" -- a verdict and the demand signals behind it. */
export function IpoSignals({ detail, context }: { detail: NseIssueDetail; context: IssueContext }) {
  const { verdict, tiles } = useMemo(() => ipoSignals(detail, context), [detail, context]);
  const vt = TONE[verdict.tone];

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <div className="col-span-2 rounded-lg border border-l-4 p-3" style={{ borderColor: "var(--mf-border)", borderLeftColor: vt.fg, background: vt.bg }}>
          <div className="flex items-center text-[0.7rem] font-semibold uppercase" style={{ color: "var(--mf-muted)" }}>
            Take a chance?
            <FormulaTooltip
              label="Take a chance?"
              description="A rule-of-thumb read of bidding demand: institutional (QIB) and HNI subscription count most, then overall subscription. While bidding is open, low numbers are not held against an issue, since most bids come on the last day. SME issues and pure offers for sale are marked down. It does not look at the company's financials, valuation or grey-market premium."
            />
          </div>
          <div className="text-lg font-bold" style={{ color: vt.fg }}>{verdict.label}</div>
          <ul className="mt-1 list-disc pl-4 text-xs" style={{ color: "var(--mf-muted)" }}>
            {verdict.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
          {verdict.asOf && (
            <div className="mt-1 text-[0.68rem]" style={{ color: "var(--mf-muted)" }}>
              NSE figures: {verdict.asOf.replace(/^updated as on\s*/i, "")}
            </div>
          )}
        </div>
        {tiles.map((t) => {
          const c = TONE[t.tone];
          return (
            <div key={t.label} className="rounded-lg border p-2.5" style={{ borderColor: "var(--mf-border)", background: c.bg }}>
              <div className="flex items-center text-[0.68rem] font-semibold uppercase" style={{ color: "var(--mf-muted)" }}>
                {t.label}
                <FormulaTooltip label={t.label} description={t.about} />
              </div>
              <div className="text-base font-bold" style={{ color: c.fg }}>{t.value}</div>
              <div className="text-[0.72rem] leading-snug" style={{ color: "var(--mf-muted)" }}>{t.sub}</div>
            </div>
          );
        })}
      </div>
      <p className="text-[0.7rem]" style={{ color: "var(--mf-muted)" }}>
        Based only on NSE&apos;s published demand and issue terms, worked out now and not saved. Not investment advice: read the red herring prospectus, and
        remember listing gains are never guaranteed.
      </p>
    </div>
  );
}
