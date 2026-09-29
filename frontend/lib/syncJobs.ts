/** Wording for the AMFI sync jobs, shared by the top bar and Data Management. */

const TRIGGERS: Record<string, string> = {
  manual: "started by you",
  "scheduled_00:05": "nightly, 00:05",
  "scheduled_23:30": "evening, 23:30",
  heartbeat: "hourly catch-up",
  startup: "on startup",
  catchup: "catch-up",
};

/** "scheduled_00:05" -> "nightly, 00:05"; unknown triggers are shown as they are. */
export function triggerText(trigger: string | null | undefined): string {
  if (!trigger) return "";
  return TRIGGERS[trigger] ?? trigger;
}

/** "3 min 20 s" / "45 s". */
export function durationText(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "";
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  return m ? `${m} min ${s % 60} s` : `${s} s`;
}

const IST_OFFSET_MS = 330 * 60_000;

/** The daemon's two fixed runs, in IST: the full refresh at 00:05 and the evening NAV sync
 *  at 23:30. The hourly catch-up is not listed; it only acts when one of these was missed. */
const SCHEDULE: { hour: number; minute: number; kind: "full" | "nav"; label: string }[] = [
  { hour: 0, minute: 5, kind: "full", label: "Full refresh" },
  { hour: 23, minute: 30, kind: "nav", label: "NAV sync" },
];

/** The next scheduled run after `nowMs`, computed in IST whatever the browser's zone. */
export function nextScheduledRun(nowMs: number): { kind: "full" | "nav"; label: string; at: number } {
  const ist = new Date(nowMs + IST_OFFSET_MS);
  const dayStartUtc = Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate()) - IST_OFFSET_MS;
  const candidates = [0, 1].flatMap((d) =>
    SCHEDULE.map((s) => ({ kind: s.kind, label: s.label, at: dayStartUtc + d * 86_400_000 + (s.hour * 60 + s.minute) * 60_000 })),
  );
  const next = candidates.filter((c) => c.at > nowMs).sort((a, b) => a.at - b.at)[0];
  return { ...next, at: next.at / 1000 };
}

/** "in 3 h 20 min" / "in 12 min". */
export function untilText(epochSeconds: number, nowMs: number): string {
  const mins = Math.max(0, Math.round((epochSeconds * 1000 - nowMs) / 60_000));
  const h = Math.floor(mins / 60);
  return h ? `in ${h} h ${mins % 60} min` : `in ${mins} min`;
}

export interface RefreshStep {
  name: string;
  ok: boolean;
  text: string;
}

type Step = Record<string, unknown> | undefined;
const n = (v: unknown) => (typeof v === "number" ? v.toLocaleString("en-IN") : "0");

/** A full refresh's result, step by step. The run itself counts as ok when its NAVs landed,
 *  so a failed enrichment (plan/option, riskometer, AUM) only shows up here. */
export function refreshSteps(result: Record<string, unknown> | null | undefined): RefreshStep[] {
  if (!result) return [];
  const step = (key: string) => result[key] as Step;
  const reason = (s: Step) => String(s?.reason ?? s?.message ?? "failed");
  const out: RefreshStep[] = [];
  const nav = step("nav");
  if (nav) out.push({ name: "NAV download", ok: !!nav.ok, text: String(nav.message ?? "") });
  const rs = step("restatement");
  if (rs)
    out.push({
      name: "Restated-NAV check",
      ok: !!rs.ok,
      text: rs.ok
        ? `${n(rs.checked)} NAVs over ${n(rs.window_days)} days re-checked: ${n(rs.restated)} restated, ${n(rs.gap_filled)} gaps filled`
        : reason(rs),
    });
  const sp = step("splits");
  if (sp) out.push({ name: "Unit-split repair", ok: !!sp.ok, text: sp.ok ? `${n(sp.rows_adjusted)} rows adjusted` : reason(sp) });
  const rv = step("resolve");
  if (rv)
    out.push({
      name: "Plan/option, riskometer & AUM",
      ok: !!rv.ok,
      text: rv.ok
        ? `${n(rv.funds_in_feed)} funds in AMFI's feed · ${n(rv.resolved)} schemes matched · ${n(rv.plan_changed)} plan and ${n(rv.option_changed)} option labels corrected · ${n(rv.riskometer_changed)} riskometers changed`
        : reason(rv),
    });
  const ter = step("ter");
  if (ter) out.push({ name: "Official TER", ok: !!ter.ok, text: String(ter.message ?? "") });
  const audit = step("audit");
  if (audit) {
    const s = audit.summary as Record<string, number> | undefined;
    out.push({
      name: "Data audit",
      ok: !!audit.ok && !(s?.errors ?? 0),
      text: audit.ok
        ? `${n(s?.errors)} error check${s?.errors === 1 ? "" : "s"} failing (${n(s?.error_rows)} rows) · ${n(s?.warnings)} warning${s?.warnings === 1 ? "" : "s"}`
        : reason(audit),
    });
  }
  return out;
}

/** Epoch seconds -> "28 Sep, 00:09". */
export function whenText(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return "-";
  return new Date(epochSeconds * 1000).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
