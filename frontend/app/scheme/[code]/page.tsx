"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import { AppShell } from "@/components/layout/AppShell";
import { Banner } from "@/components/shared/Banner";
import { NonAdviceDisclaimer } from "@/components/shared/Disclaimer";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { StatCard } from "@/components/shared/StatCard";
import { FeeDragPanel } from "@/components/quant/FeeDragPanel";
import { getFeeDrag, getQuantAnalysis } from "@/lib/api/quant";
import { getSchemeProfile, getSchemeTerHistory } from "@/lib/api/schemes";
import { formatDate, formatSignedPct } from "@/lib/format";
import { useDateRangeStore } from "@/lib/stores/dateRange";

export default function SchemeDossierPage() {
  const params = useParams<{ code: string }>();
  const code = Number(params.code);
  const { start, end } = useDateRangeStore();

  const { data: profile, isError } = useQuery({
    queryKey: ["scheme-profile", code],
    queryFn: () => getSchemeProfile(code),
    enabled: Number.isFinite(code),
  });
  const { data: terHist } = useQuery({
    queryKey: ["scheme-ter-hist", code],
    queryFn: () => getSchemeTerHistory(code),
    enabled: Number.isFinite(code),
  });
  const { data: quant } = useQuery({
    queryKey: ["scheme-quant", code, start, end],
    queryFn: () => getQuantAnalysis(code, { startDate: start, endDate: end }),
    enabled: Number.isFinite(code) && !!start && !!end,
  });
  const { data: feeDrag } = useQuery({
    queryKey: ["scheme-fee-drag", code],
    queryFn: () => getFeeDrag(code),
    enabled: Number.isFinite(code),
  });

  const spark = {
    data: [
      {
        type: "scatter",
        mode: "lines",
        x: (terHist ?? []).map((r) => r.ter_date),
        y: (terHist ?? []).map((r) => r.total_ter_pct),
        line: { color: "#2563EB", width: 2 },
      },
    ],
    layout: { height: 220, margin: { t: 10, b: 30, l: 40, r: 10 }, yaxis: { ticksuffix: "%" } },
  };

  return (
    <AppShell>
      <h1 className="mf-page-title">{profile?.scheme_name ?? `Scheme ${code}`}</h1>
      <p className="mf-page-caption">Profile-only dossier. No AUM. NAV history is not loaded unbounded on this page.</p>
      <div className="mt-3">
        <NonAdviceDisclaimer compact />
      </div>
      {isError && <Banner level="danger">Scheme not found.</Banner>}
      {profile && (
        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatCard title="AMC" value={String(profile.fund_house ?? "—")} />
          <StatCard title="Plan / Option" value={`${profile.plan_type ?? "—"} / ${profile.option_type ?? "—"}`} />
          <StatCard title="ISIN" value={String(profile.isin ?? "—")} />
          <StatCard title="Latest NAV" value={profile.latest_nav != null ? String(profile.latest_nav) : "—"} sub={profile.latest_date ? formatDate(profile.latest_date) : undefined} />
          <StatCard title="TER status" value={String(profile.ter_status ?? "unknown")} />
          <StatCard title="Expense ratio" value={profile.expense_ratio != null ? `${Number(profile.expense_ratio).toFixed(4)}%` : "—"} />
          <StatCard title="1Y NAV return" value={formatSignedPct(profile.return_1y_pct)} />
          <StatCard title="52w range" value={profile.high_52w != null ? `${profile.low_52w} – ${profile.high_52w}` : "—"} />
        </div>
      )}
      <h2 className="mt-6 text-base font-bold">Official TER history (Reg 66)</h2>
      {(terHist ?? []).length === 0 ? (
        <Banner level="info">No dated TER history for this scheme yet.</Banner>
      ) : (
        <PlotlyChart figure={spark} />
      )}
      {quant?.metrics && (
        <>
          <h2 className="mt-6 text-base font-bold">Quant snapshot (selected window)</h2>
          <div className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatCard title="CAGR" value={formatSignedPct(quant.metrics.cagr_pct)} />
            <StatCard title="Sharpe" value={quant.metrics.sharpe_ratio.toFixed(3)} />
            <StatCard title="Max DD" value={formatSignedPct(quant.metrics.max_drawdown_pct)} />
            <StatCard title="Trading days" value={String(quant.coverage.n_trading_days)} />
          </div>
        </>
      )}
      <h2 className="mt-6 text-base font-bold">Fee drag vs pair</h2>
      <div className="mt-2">
        <FeeDragPanel data={feeDrag as Record<string, unknown> | undefined} />
      </div>
      <div className="mt-6 flex flex-wrap gap-3 text-sm">
        <Link href={`/quant?scheme_code=${code}`} style={{ color: "var(--mf-accent)" }}>Quant</Link>
        <Link href={`/compare`} style={{ color: "var(--mf-accent)" }}>Compare</Link>
        <Link href={`/portfolio`} style={{ color: "var(--mf-accent)" }}>Portfolio</Link>
      </div>
    </AppShell>
  );
}
