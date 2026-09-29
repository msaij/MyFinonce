"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { Banner } from "@/components/shared/Banner";
import { DataTable, type ColumnConfig } from "@/components/shared/DataTable";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { StatCard } from "@/components/shared/StatCard";
import { FeeDragPanel } from "@/components/quant/FeeDragPanel";
import { getHoldingsSummary } from "@/lib/api/holdings";
import { getFeeDrag, getQuantAnalysis } from "@/lib/api/quant";
import { getNavHistory, getSchemeProfile, getSchemeTerHistory, type SchemeProfile } from "@/lib/api/schemes";
import { formatDate, formatInr, formatSignedPct, toneOf } from "@/lib/format";
import { formatSignedInr, formatUnits } from "@/lib/holdings";
import { AXIS, SERIES_1, SERIES_2 } from "@/lib/holdingsChart";
import { useDateRangeStore } from "@/lib/stores/dateRange";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { RISK_ABOUT, riskRank } from "@/lib/riskometer";

/** Every horizon summary_table carries, shortest first. `years` marks the ones whose
 *  stored figure is CUMULATIVE over that span -- printing "3 years 22.3%" beside
 *  "1 year 6.4%" without saying so reads as a collapse in performance when it is the
 *  opposite, so those rows carry an annualised figure alongside. `rank` is the peer
 *  percentile the API returns for that horizon (only three are rankable). */
const HORIZONS: { key: keyof SchemeProfile; label: string; years?: number; rank?: "1y" | "3y" | "5y" }[] = [
  { key: "change_1d_pct", label: "1 day" },
  { key: "return_7d_pct", label: "1 week" },
  { key: "return_30d_pct", label: "1 month" },
  { key: "return_90d_pct", label: "3 months" },
  { key: "return_1y_pct", label: "1 year", rank: "1y" },
  { key: "return_3y_pct", label: "3 years", years: 3, rank: "3y" },
  { key: "return_5y_pct", label: "5 years", years: 5, rank: "5y" },
  { key: "return_10y_pct", label: "10 years", years: 10 },
];

const TER_PARTS: { key: keyof SchemeProfile; label: string }[] = [
  { key: "ter_base_expense_ratio", label: "base" },
  { key: "ter_brokerage_cost_pct", label: "brokerage" },
  { key: "ter_transaction_cost_pct", label: "transaction costs" },
  { key: "ter_statutory_levies_pct", label: "statutory levies" },
];

/** Cumulative % over `years` -> the constant annual rate that compounds to it. */
function annualised(cumulativePct: number, years: number): number | null {
  if (cumulativePct <= -100) return null;
  return (Math.pow(1 + cumulativePct / 100, 1 / years) - 1) * 100;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export default function SchemeDossierPage() {
  const params = useParams<{ code: string }>();
  const code = Number(params.code);
  const { start, end } = useDateRangeStore();
  const enabled = Number.isFinite(code);

  const { data: profile, isLoading, isError, error } = useQuery({
    queryKey: ["scheme-profile", code],
    queryFn: () => getSchemeProfile(code),
    enabled,
    retry: false,
  });
  const { data: terHist } = useQuery({ queryKey: ["scheme-ter-hist", code], queryFn: () => getSchemeTerHistory(code), enabled });
  const { data: navHist } = useQuery({
    queryKey: ["scheme-nav", code, start, end],
    queryFn: () => getNavHistory([code], start, end),
    enabled: enabled && !!start && !!end,
  });
  const { data: quant } = useQuery({
    queryKey: ["scheme-quant", code, start, end],
    queryFn: () => getQuantAnalysis(code, { startDate: start, endDate: end }),
    enabled: enabled && !!start && !!end,
  });
  const { data: feeDrag } = useQuery({ queryKey: ["scheme-fee-drag", code], queryFn: () => getFeeDrag(code), enabled });
  // The owner's own book, so the dossier can answer "do I hold this?" without a detour
  // through Holdings. A household with no portfolios yet simply yields no position.
  const { data: holdings } = useQuery({
    queryKey: ["holdings", "summary", "all"],
    queryFn: () => getHoldingsSummary("all"),
    retry: false,
  });
  const position = holdings?.positions?.find((p) => p.scheme_code === code && !p.is_closed);

  const navFig = useMemo(() => {
    if (!navHist?.length) return null;
    return {
      data: [{
        type: "scatter", mode: "lines", name: "NAV",
        x: navHist.map((p) => p.nav_date), y: navHist.map((p) => p.nav),
        line: { color: SERIES_1, width: 2 },
        hovertemplate: "%{x}<br>NAV %{y:.4f}<extra></extra>",
      }],
      layout: {
        height: 300, margin: { t: 10, l: 65, r: 10 }, showlegend: false,
        yaxis: { ...AXIS, title: { text: "NAV (₹)" } }, xaxis: { ...AXIS },
      },
    };
  }, [navHist]);

  const terFig = useMemo(() => {
    if (!terHist?.length) return null;
    const rows = [...terHist].sort((a, b) => a.ter_date.localeCompare(b.ter_date));
    // Each row is an interval, not a sample, so it is plotted as its two endpoints and the
    // line is stepped. Joining one change point to the next with a straight segment would
    // draw a fund gliding from 0.82% to 0.79% over seven weeks, when it actually held 0.82%
    // and then changed on a single day.
    const step = <K extends "total_ter_pct" | "base_expense_ratio_pct">(key: K) => {
      const x: string[] = [];
      const y: (number | null)[] = [];
      for (const r of rows) {
        x.push(r.ter_date);
        y.push(r[key]);
        const end = r.valid_to ?? r.ter_date;
        if (end !== r.ter_date) {
          x.push(end);
          y.push(r[key]);
        }
      }
      return { x, y };
    };
    const total = step("total_ter_pct");
    const base = step("base_expense_ratio_pct");
    return {
      data: [
        {
          type: "scatter", mode: "lines", name: "Total TER",
          x: total.x, y: total.y,
          line: { color: SERIES_1, width: 2, shape: "hv" }, hovertemplate: "%{x}<br>Total %{y:.4f}%<extra></extra>",
        },
        {
          type: "scatter", mode: "lines", name: "Base expense ratio",
          x: base.x, y: base.y,
          line: { color: SERIES_2, width: 1.5, dash: "dot", shape: "hv" }, hovertemplate: "%{x}<br>Base %{y:.4f}%<extra></extra>",
        },
      ],
      layout: {
        height: 260, margin: { t: 30, l: 55, r: 10 }, legend: { orientation: "h", y: 1.15 },
        yaxis: { ...AXIS, ticksuffix: "%", rangemode: "tozero" }, xaxis: { ...AXIS },
      },
    };
  }, [terHist]);

  const returnRows = useMemo(() => {
    if (!profile) return [];
    return HORIZONS.map(({ key, label, years, rank }) => {
      const value = num(profile[key]);
      const peer = rank ? profile.peer_rank?.[rank] : null;
      return {
        label,
        value,
        annualised: value !== null && years ? annualised(value, years) : null,
        basis: years ? `cumulative over ${years} years` : "",
        percentile: peer?.percentile ?? null,
        peers: peer?.peers ?? null,
      };
    }).filter((r) => r.value !== null);
  }, [profile]);

  const returnColumns: ColumnConfig[] = useMemo(
    () => [
      { key: "label", label: "Period", sortable: false },
      {
        key: "value", label: "Return", sortable: false,
        render: (r, v) => (
          <span className={toneOf(v as number) === "pos" ? "mf-pos" : toneOf(v as number) === "neg" ? "mf-neg" : ""}>
            {formatSignedPct(v as number, 2)}
            {r.basis ? <span className="block text-[0.7rem]" style={{ color: "var(--mf-muted)" }}>{String(r.basis)}</span> : null}
          </span>
        ),
      },
      {
        key: "annualised", label: "Annualised", sortable: false,
        render: (_r, v) => (v === null ? <span style={{ color: "var(--mf-muted)" }}>—</span> : formatSignedPct(v as number, 2)),
      },
      {
        key: "percentile", label: "Vs its peer group", sortable: false,
        render: (r, v) =>
          v === null ? (
            <span style={{ color: "var(--mf-muted)" }}>—</span>
          ) : (
            <span>
              {Math.round(v as number)}th percentile
              <span className="block text-[0.7rem]" style={{ color: "var(--mf-muted)" }}>
                of {String(r.peers)} {profile?.plan_type ?? ""} funds in the category
              </span>
            </span>
          ),
      },
    ],
    [profile?.plan_type]
  );

  if (!enabled) return <AppShell><Banner level="danger">&quot;{params.code}&quot; is not a scheme code.</Banner></AppShell>;
  if (isLoading) return <AppShell><div className="text-sm" style={{ color: "var(--mf-muted)" }}>Loading scheme {code}…</div></AppShell>;
  if (isError || !profile) {
    return (
      <AppShell>
        <h1 className="mf-page-title">Scheme {code}</h1>
        <div className="mt-4"><Banner level="danger">{(error as Error)?.message ?? `No scheme with AMFI code ${code}.`}</Banner></div>
        <div className="mt-4 text-sm"><Link href="/screener" style={{ color: "var(--mf-accent)" }}>Find a fund in the Screener →</Link></div>
      </AppShell>
    );
  }

  const terParts = TER_PARTS.filter(({ key }) => num(profile[key]) !== null);
  const paired = num(feeDrag?.paired_scheme_code);
  const badges = [
    profile.plan_type,
    profile.option_type,
    profile.is_active === false ? "Stale NAV" : null,
    position ? "In your holdings" : null,
  ].filter(Boolean) as string[];

  return (
    <AppShell>
      <h1 className="mf-page-title">{profile.display_name ?? profile.scheme_name}</h1>
      <p className="mf-page-caption">
        {[profile.fund_house, profile.category].filter(Boolean).join(" · ")}
        {profile.isin ? ` · ISIN ${profile.isin}` : ""}
      </p>
      <div className="mt-2 flex flex-wrap gap-1">
        {badges.map((b) => (
          <span
            key={b}
            className="rounded-full border px-2 py-0.5 text-[0.7rem] font-semibold"
            style={{ borderColor: "var(--mf-border)", color: b === "Stale NAV" ? "var(--mf-warning)" : "var(--mf-muted)" }}
          >
            {b}
          </span>
        ))}
      </div>

      {profile.is_active === false && (
        <div className="mt-3">
          <Banner level="warning">
            This scheme has stopped publishing NAVs. It may have been merged, wound up, or renamed to a new AMFI code.
          </Banner>
        </div>
      )}

      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard
          title="Latest NAV"
          value={profile.latest_nav != null ? Number(profile.latest_nav).toFixed(4) : "—"}
          sub={profile.latest_date ? `as of ${formatDate(profile.latest_date)}` : ""}
        />
        <StatCard
          title="1-day change"
          value={formatSignedPct(num(profile.change_1d_pct), 2)}
          tone={toneOf(num(profile.change_1d_pct))}
        />
        <StatCard
          title="1-year return"
          value={formatSignedPct(num(profile.return_1y_pct), 2)}
          tone={toneOf(num(profile.return_1y_pct))}
          sub={profile.peer_rank?.["1y"] ? `${Math.round(profile.peer_rank["1y"]!.percentile)}th percentile of ${profile.peer_rank["1y"]!.peers} peers` : ""}
        />
        <StatCard
          title="Expense ratio"
          value={profile.expense_ratio != null ? `${Number(profile.expense_ratio).toFixed(4)}%` : "—"}
          sub={profile.ter_as_of_date ? `${profile.ter_status ?? ""} · ${formatDate(profile.ter_as_of_date)}` : String(profile.ter_status ?? "unknown")}
          tone={profile.ter_status === "official" ? "" : "warn"}
        />
        <StatCard
          title="52-week range"
          // Both are MIN/MAX of nav_history.nav -- per-unit prices, so 4 decimals like
          // every other NAV, including the Latest NAV card beside this one.
          value={profile.low_52w != null && profile.high_52w != null ? `${Number(profile.low_52w).toFixed(4)} – ${Number(profile.high_52w).toFixed(4)}` : "—"}
          sub={num(profile.dist_from_52w_high_pct) !== null ? `${Number(profile.dist_from_52w_high_pct).toFixed(2)}% below its high` : ""}
        />
        <StatCard title="Category" value={String(profile.category ?? "—")} sub={String(profile.broad_category ?? "")} />
        <StatCard title="AMC" value={String(profile.fund_house ?? "—")} />
        {/* In the ISIN card's place: the ISIN is already in the caption above. */}
        <StatCard
          title="Riskometer"
          value={profile.riskometer ?? "Not published"}
          sub={
            profile.riskometer
              ? `Level ${riskRank(profile.riskometer)} of 6${profile.riskometer_as_of ? ` · as of ${formatDate(profile.riskometer_as_of)}` : ""}`
              : "AMFI publishes none for this scheme"
          }
          tooltip={<FormulaTooltip label="Riskometer" description={RISK_ABOUT} />}
        />
      </div>

      {position && (
        <section className="mt-6">
          <h2 className="text-base font-bold">Your position</h2>
          <div className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatCard title="Units" value={formatUnits(position.units)} sub={position.avg_cost_nav ? `Avg cost ${position.avg_cost_nav.toFixed(4)}` : ""} />
            <StatCard title="Value" value={formatInr(position.current_value)} />
            <StatCard
              title="Unrealised gain"
              value={formatSignedInr(position.unrealised_gain)}
              tone={toneOf(position.unrealised_gain)}
              sub={formatSignedPct(position.unrealised_pct, 2)}
              subTone={toneOf(position.unrealised_pct)}
            />
            <StatCard title="XIRR" value={position.xirr_pct !== null ? formatSignedPct(position.xirr_pct, 2) : "—"} tone={toneOf(position.xirr_pct)} />
          </div>
          <div className="mt-2 text-sm">
            <Link href="/holdings" style={{ color: "var(--mf-accent)" }}>Open in Holdings →</Link>
          </div>
        </section>
      )}

      <section className="mt-6">
        <h2 className="text-base font-bold">Returns by period</h2>
        <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
          NAV-to-NAV, as of {formatDate(profile.latest_date)}. Periods beyond a year are the total change over the whole span, with the
          equivalent annual rate beside them. Peer groups are active funds in the same category and plan type.
        </p>
        <div className="mt-2">
          <DataTable columns={returnColumns} rows={returnRows as unknown as Record<string, unknown>[]} keyField="label" />
        </div>
      </section>

      <section className="mt-6">
        <h2 className="text-base font-bold">NAV over the selected window</h2>
        <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
          {start && end ? `${formatDate(start)} to ${formatDate(end)}. Change the window from the date picker above.` : "Pick a date window above."}
        </p>
        {navFig ? <PlotlyChart figure={navFig} /> : <Banner level="info">No NAV data for this window.</Banner>}
      </section>

      {quant?.metrics && (
        <section className="mt-6">
          <h2 className="text-base font-bold">Risk and return over that window</h2>
          <div className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-5">
            <StatCard title="CAGR" value={formatSignedPct(quant.metrics.cagr_pct, 2)} tone={toneOf(quant.metrics.cagr_pct)} />
            <StatCard title="Volatility" value={`${quant.metrics.vol_annualized_pct.toFixed(2)}%`} sub="annualised" />
            <StatCard title="Sharpe" value={quant.metrics.sharpe_ratio.toFixed(2)} />
            <StatCard title="Max drawdown" value={formatSignedPct(quant.metrics.max_drawdown_pct, 2)} tone="neg" />
            <StatCard title="Trading days" value={String(quant.coverage.n_trading_days)} sub={quant.coverage.is_partial ? "partial window" : ""} />
          </div>
          <div className="mt-2 text-sm">
            <Link href={`/quant?scheme_code=${code}`} style={{ color: "var(--mf-accent)" }}>Full quant analysis →</Link>
          </div>
        </section>
      )}

      <section className="mt-6">
        <h2 className="text-base font-bold">What it costs</h2>
        {terParts.length > 0 && profile.expense_ratio != null && (
          <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
            TER {Number(profile.expense_ratio).toFixed(4)}% = {terParts.map(({ key, label }) => `${label} ${Number(profile[key]).toFixed(4)}`).join(" + ")}.
            {profile.ter_as_of_date ? ` AMFI disclosure of ${formatDate(profile.ter_as_of_date)}.` : ""}
            {profile.ter_source_url ? (
              <>
                {" "}
                <a href={String(profile.ter_source_url)} target="_blank" rel="noreferrer" style={{ color: "var(--mf-accent)" }}>Source</a>.
              </>
            ) : null}
          </p>
        )}
        {terFig ? (
          <div className="mt-2">
            <PlotlyChart figure={terFig} />
            <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
              {terHist!.length} distinct TER {terHist!.length === 1 ? "period" : "periods"} on record. AMFI discloses daily; identical
              consecutive days are stored once, so the line steps at each change rather than
              sliding between them. The gap between the two lines is brokerage, transaction costs and statutory levies.
            </p>
          </div>
        ) : (
          <div className="mt-2"><Banner level="info">No dated TER history for this scheme yet.</Banner></div>
        )}
      </section>

      <section className="mt-6">
        <h2 className="text-base font-bold">Direct vs Regular</h2>
        <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
          The same portfolio in both plans; Regular carries the distributor commission inside its expense ratio, which compounds away over time.
        </p>
        <div className="mt-2">
          <FeeDragPanel data={feeDrag as Record<string, unknown> | undefined} />
        </div>
        {paired !== null && (
          <div className="mt-2 text-sm">
            <Link href={`/scheme/${paired}`} style={{ color: "var(--mf-accent)" }}>
              Open its {profile.plan_type === "Direct" ? "Regular" : "Direct"} twin [{paired}] →
            </Link>
          </div>
        )}
      </section>

      <div className="mt-8 flex flex-wrap gap-4 border-t pt-4 text-sm" style={{ borderColor: "var(--mf-border)" }}>
        <Link href={`/quant?scheme_code=${code}`} style={{ color: "var(--mf-accent)" }}>Quant analysis</Link>
        <Link href={`/compare?codes=${[code, paired].filter((c) => c !== null).join(",")}`} style={{ color: "var(--mf-accent)" }}>
          Compare{paired !== null ? " with its twin" : ""}
        </Link>
        {profile.category && (
          <Link href={`/screener?sub_cat=${encodeURIComponent(String(profile.category))}`} style={{ color: "var(--mf-accent)" }}>
            Its category in the Screener
          </Link>
        )}
        {profile.fund_house && (
          <Link href={`/screener?amc=${encodeURIComponent(String(profile.fund_house))}`} style={{ color: "var(--mf-accent)" }}>
            Everything from this AMC
          </Link>
        )}
      </div>
      <p className="mt-6 text-xs" style={{ color: "var(--mf-muted)" }}>
        NAV, returns and expense ratios come from AMFI. Returns are NAV-to-NAV and ignore exit loads and taxes. Nothing here is investment advice.
      </p>
    </AppShell>
  );
}
