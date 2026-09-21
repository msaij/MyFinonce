"use client";

import { Banner } from "@/components/shared/Banner";
import { StatCard } from "@/components/shared/StatCard";
import { formatInr, formatSignedPct } from "@/lib/format";

type SchemeLeg = {
  scheme_code?: number | null;
  scheme_name?: string | null;
  expense_ratio_pct?: number | null;
  ter_status?: string | null;
};

type HorizonRow = {
  wealth_direct?: number;
  wealth_regular?: number;
  rupee_wealth_erosion?: number;
  cumulative_drag_pct?: number;
  cagr_spread_pct?: number;
};

export function FeeDragPanel({ data }: { data: Record<string, unknown> | undefined }) {
  if (!data) return <Banner level="info">Fee-drag has not been computed yet.</Banner>;
  const status = String(data.status ?? "ok");
  const schemes = (data.schemes ?? {}) as { direct_scheme?: SchemeLeg; regular_scheme?: SchemeLeg };
  const horizons = (data.horizons ?? {}) as Record<string, HorizonRow>;
  const params = (data.parameters ?? {}) as { ter_direct_pct?: number | null; ter_regular_pct?: number | null; ter_model?: string };
  return (
    <div className="space-y-3">
      {status === "unpaired" && <Banner level="warning">No Direct/Regular pair; pair drag not computed.</Banner>}
      {status === "unavailable" && (
        <Banner level="warning">{String(data.message ?? "Missing official TER or empirical CAGR; fee-drag not invented.")}</Banner>
      )}
      {typeof data.ter_banner === "string" && data.ter_banner && <Banner level="info">{data.ter_banner}</Banner>}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <StatCard
          title="Direct TER"
          value={params.ter_direct_pct != null ? `${params.ter_direct_pct.toFixed(4)}%` : "—"}
          sub={`${schemes.direct_scheme?.ter_status ?? "unknown"} · ${schemes.direct_scheme?.scheme_name ?? "—"}`}
        />
        <StatCard
          title="Regular TER"
          value={params.ter_regular_pct != null ? `${params.ter_regular_pct.toFixed(4)}%` : "—"}
          sub={`${schemes.regular_scheme?.ter_status ?? "unknown"} · ${schemes.regular_scheme?.scheme_name ?? "—"}`}
        />
      </div>
      {status === "ok" && Object.keys(horizons).length > 0 && (
        <div className="overflow-x-auto rounded-xl border" style={{ borderColor: "var(--mf-border)" }}>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b text-left" style={{ borderColor: "var(--mf-border)", color: "var(--mf-muted)" }}>
                <th className="px-3 py-2">Horizon</th>
                <th className="px-3 py-2">Direct wealth</th>
                <th className="px-3 py-2">Regular wealth</th>
                <th className="px-3 py-2">Rupee erosion</th>
                <th className="px-3 py-2">Cum. drag</th>
                <th className="px-3 py-2">CAGR spread</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(horizons).map(([h, row]) => (
                <tr key={h} className="border-b last:border-0" style={{ borderColor: "var(--mf-border)" }}>
                  <td className="px-3 py-2 font-semibold">{h}</td>
                  <td className="px-3 py-2">{formatInr(row.wealth_direct ?? null)}</td>
                  <td className="px-3 py-2">{formatInr(row.wealth_regular ?? null)}</td>
                  <td className="px-3 py-2">{formatInr(row.rupee_wealth_erosion ?? null)}</td>
                  <td className="px-3 py-2">{formatSignedPct(row.cumulative_drag_pct ?? null)}</td>
                  <td className="px-3 py-2">{formatSignedPct(row.cagr_spread_pct ?? null)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
